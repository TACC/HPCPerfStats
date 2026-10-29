"""
In-process job queues for the sync_timedb orchestrator.

Durable queue snapshots live under the archive directory. In-flight claims
and owner tokens stay in memory and are reconstructed after a crash.

Attributes:
  ARCHIVE_MEMBERS_STORE_DIR_RELPATH: Sidecar directory basename for members.
  CATCHUP_SCORE_BASE: Score floor for catchup-band ingest members.
  ClaimedJob: One atomically claimed job.
  HOT_SCORE_BASE: Score floor for hot-band ingest members.
  INFLIGHT_REAP_GRACE_FLOOR_S: Grace seconds past an in-flight deadline.
  JOB_ATTEMPT_MAX_DEFAULT: Default attempt ceiling before dead-lettering.
  JOB_KIND_APPEND: Job kind string for the append LIST queue.
  JOB_KIND_DAY_CLOSE: Job kind string for the day_close LIST queue.
  JOB_KIND_DISCOVER: Job kind string for the discover LIST queue.
  JOB_KIND_INGEST: Job kind string for the ingest score map.
  JOB_KINDS_ALL: Tuple of every durable job kind.
  JOB_KINDS_LIST: Tuple of kinds that use FIFO lists.
  JOB_LEASE_TTL_FLOOR_S: Minimum claim deadline seconds.
  JOB_STORE_PERSIST_INTERVAL_S: Default snapshot write interval.
  JOB_STORE_SNAPSHOT_KIND: Persistence registry kind for the job snapshot.
  JOB_STORE_SNAPSHOT_RELPATH: Sidecar basename for the job snapshot.
  INGEST_PENALTY_STEP: Score bump per deprioritizing requeue; the result is
    clamped inside the file's own day (``penalize_ingest_score``).
  LeaseOwner: Parsed owner token.
  QUEUE_DEAD_LETTER_KIND: Persistence kind for queue dead letters.
  NON_FATAL_DEAD_LETTER_REASONS: Legacy dead-letter reasons current code no
    longer treats as fatal (fill skips); re-admitted immediately (DE-21).
  _DEAD_LETTER_INDEX_CACHE: ``sidecar path -> (mtime_ns, index)`` cache for the
    per-path dead-letter predicate.
  _DEAD_LETTER_LOCK: Guards ``_DEAD_LETTER_INDEX_CACHE``.
  _DEAD_LETTER_WRITE_LOCK: Serializes dead-letter sidecar writers.
  QUEUE_MAX_MEMBERS_FLOOR: Minimum accepted queue capacity bound.
  SCORE_STRIDE: Day/tie-break stride inside a band score range.
  SyncTimedbJobStore: Thread-safe in-process store.
  _BOOT_ID_CACHE: Memoized host boot identifier.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import secrets
import socket
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from itertools import islice
from typing import Any

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  artifact_path,
  load_persistence_document,
  save_persistence_document,
)
from hpcperfstats.dbload.lib.sync_timedb_store_lock_timing import TimedRLock
from hpcperfstats.dbload.lib.sync_timedb_write_behind import WriteBehindWriter

JOB_KIND_DISCOVER = "discover"
JOB_KIND_INGEST = "ingest"
JOB_KIND_APPEND = "append"
JOB_KIND_DAY_CLOSE = "day_close"
JOB_KINDS_LIST = (JOB_KIND_DISCOVER, JOB_KIND_APPEND, JOB_KIND_DAY_CLOSE)
JOB_KINDS_ALL = (JOB_KIND_INGEST, *JOB_KINDS_LIST)

HOT_SCORE_BASE = 0
CATCHUP_SCORE_BASE = 10**15
SCORE_STRIDE = 10**6
INGEST_PENALTY_STEP = SCORE_STRIDE // 4
JOB_ATTEMPT_MAX_DEFAULT = 5
QUEUE_MAX_MEMBERS_FLOOR = 1000
QUEUE_DEAD_LETTER_KIND = "queue_dead_letter"
JOB_STORE_SNAPSHOT_KIND = "job_store_snapshot"
JOB_STORE_SNAPSHOT_RELPATH = ".sync_timedb_job_store.json"
ARCHIVE_MEMBERS_STORE_DIR_RELPATH = ".sync_timedb_archive_members"
JOB_STORE_PERSIST_INTERVAL_S = 60.0
JOB_LEASE_TTL_FLOOR_S = 60
INFLIGHT_REAP_GRACE_FLOOR_S = 30

_BOOT_ID_CACHE: str | None = None


@dataclass(frozen=True)
class ClaimedJob:
  """
  One atomically claimed job.

  Attributes:
    kind: Job kind the claim came from.
    identity: Job identity (ingest path or LIST element).
    owner_token: Owner token proving this claim.
    deadline: Epoch seconds after which a reaper may recover the job.
    score: Original ingest score, or None for LIST kinds.
    fingerprint: Optional payload fingerprint; empty when absent.
    emit: Ingest payload mode. ``insert`` retries with per-row INSERT.
      Empty means COPY bytes.
  """

  kind: str
  identity: str
  owner_token: str
  deadline: float
  score: float | None
  fingerprint: str = ""
  emit: str = ""


@dataclass(frozen=True)
class LeaseOwner:
  """
  Parsed lease owner token.

  Attributes:
    nonce: Random per-acquisition component.
    hostname: Host that minted the token (empty for legacy tokens).
    boot_id: Boot identifier of that host (empty for legacy tokens).
    pid: Owning process id, or None when unparsable.
  """

  nonce: str
  hostname: str
  boot_id: str
  pid: int | None


class SyncTimedbJobStore:
  """
  Thread-safe in-process job queues with a durable snapshot of queued work.

  In-flight claims are memory-only. A restart reloads queued identities
  from the persistence sidecar and leaves reconstruct to refill anything
  that was claimed when the process died.

  Attributes:
    archive_dir: Archive data directory that owns the snapshot sidecar.
    persist_interval_s: Minimum seconds between automatic snapshot writes.
    _dirty: True when durable queues changed since the last snapshot.
    _ingest: Identity to score map for queued ingest work.
    _ingest_heap_catchup: Lazy min-heap of catchup-band ``(score, id)``.
    _ingest_heap_catchup_max: Lazy max-heap of catchup-band
      ``(-score, id)``; finds the youngest queued backfill file to push out.
    _ingest_heap_hot: Lazy min-heap of hot-band ``(score, id)``.
    _inflight: Per-kind in-flight map of deadline, owner, and score.
    _leases: Owner token by (kind, identity).
    _lists: FIFO deques for discover, append, and day_close.
    _lock: Re-entrant lock covering every mutation.
    _payloads: Fingerprint and attempt fields by (kind, identity).
    _pending: Dedupe sets mirroring queued LIST identities.
    _writer: Write-behind writer owning the ``job-store-persist`` thread.
    _shadow_ingest: Incremental durable ingest score map for persist.
    _shadow_lists: Incremental durable LIST queues for persist.
    _shadow_payloads: Incremental queued-only payload rows for persist.
    _shadow_pending: Incremental LIST dedupe sets for persist.
    _ingest_hot_n: O(1) queued hot-band ingest count.
    _ingest_catchup_n: O(1) queued catchup-band ingest count.
  """

  def __init__(
    self,
    archive_dir: str,
    *,
    persist_interval_s: float = JOB_STORE_PERSIST_INTERVAL_S,
  ) -> None:
    """
    Create an empty store and reload any on-disk snapshot.

    Args:
      archive_dir (str): Archive data directory that owns the sidecar.
      persist_interval_s (float): Minimum seconds between automatic
        snapshot writes.

    Returns:
      None

    Examples:
      >>> SyncTimedbJobStore("/tmp/archive").snapshot_is_empty()
      True
    """
    self.archive_dir = str(archive_dir)
    self.persist_interval_s = float(persist_interval_s)
    self._lock = TimedRLock("job_store")
    self._ingest: dict[str, float] = {}
    # Lazy min-heaps per score band; stale (score, ident) entries are
    # skipped on claim when _ingest no longer matches.
    self._ingest_heap_hot: list[tuple[float, str]] = []
    self._ingest_heap_catchup: list[tuple[float, str]] = []
    self._ingest_heap_catchup_max: list[tuple[float, str]] = []
    self._ingest_hot_n = 0
    self._ingest_catchup_n = 0
    self._lists: dict[str, deque[str]] = {
      kind: deque() for kind in JOB_KINDS_LIST
    }
    self._pending: dict[str, set[str]] = {
      kind: set() for kind in JOB_KINDS_LIST
    }
    self._inflight: dict[str, dict[str, tuple[float, str, float | None]]] = {
      kind: {} for kind in JOB_KINDS_ALL
    }
    self._leases: dict[tuple[str, str], str] = {}
    self._payloads: dict[tuple[str, str], dict[str, str]] = {}
    self._shadow_ingest: dict[str, float] = {}
    self._shadow_lists: dict[str, list[str]] = {
      kind: [] for kind in JOB_KINDS_LIST
    }
    self._shadow_pending: dict[str, set[str]] = {
      kind: set() for kind in JOB_KINDS_LIST
    }
    self._shadow_payloads: dict[tuple[str, str], dict[str, str]] = {}
    self._writer = WriteBehindWriter(
      name="job-store-persist",
      write_fn=self._persist_write,
      interval_s=self.persist_interval_s,
    )
    if self.archive_dir:
      os.makedirs(self.archive_dir, exist_ok=True)
      self.load()
      self._start_persist_writer()

  def ingest_identities(self) -> list[str]:
    """
    Return queued ingest identities in claim order.

    Returns:
      list[str]: Identities currently in the ingest heap, lowest score
      first.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").ingest_identities()
      []
    """
    with self._lock:
      items = list(self._ingest.items())
    return [ident for ident, _score in sorted(items, key=lambda item: item[1])]

  def inflight_count(self, kind: str) -> int:
    """
    Return how many claims are currently in flight for a kind.

    Args:
      kind (str): Job kind.

    Returns:
      int: In-flight count.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").inflight_count("ingest")
      0
    """
    with self._lock:
      return len(self._inflight.get(str(kind), {}))

  def snapshot_is_empty(self) -> bool:
    """
    Return True when no durable queue members are present.

    An empty snapshot is a hint, not proof that the archive is caught up.

    Returns:
      bool: True when ingest and LIST queues are empty.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").snapshot_is_empty()
      True
    """
    with self._lock:
      if self._ingest:
        return False
      return all(not items for items in self._lists.values())

  def persist(self, *, force: bool = False) -> None:
    """
    Notify the dedicated persist writer (or force-flush through it).

    Hot paths never encode or write the sidecar. Only the
    ``job-store-persist`` thread calls ``save_persistence_document``.
    In-flight claims and owner tokens stay omitted from the snapshot.

    Args:
      force (bool): Wake the writer immediately and wait until it
        finishes one flush attempt.

    Returns:
      None

    Raises:
      OSError: When a forced flush fails to write the sidecar.
      TimeoutError: When a forced flush does not finish in time.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.persist(force=True)
    """
    if not self.archive_dir:
      return
    if force:
      self._writer.flush()

  def close(self) -> None:
    """
    Stop the dedicated persist writer (tests and orchestrator shutdown).

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.close()
    """
    self._writer.stop()

  @property
  def _dirty(self) -> bool:
    """
    True when durable queues changed since the last snapshot.

    Returns:
      bool: The write-behind writer's dirty flag.

    Examples:
      >>> SyncTimedbJobStore("")._dirty
      False
    """
    return self._writer.dirty

  @_dirty.setter
  def _dirty(self, value: bool) -> None:
    """
    Set or clear the dirty flag (``load`` clears it after a restore).

    Args:
      value (bool): True marks dirty; False clears it.

    Returns:
      None

    Examples:
      >>> s = SyncTimedbJobStore("")
      ... s._dirty = False
    """
    if value:
      self._writer.mark_dirty()
    else:
      self._writer.clear_dirty()

  @property
  def _persist_gen(self) -> int:
    """
    Dirty-mark generation counter.

    Returns:
      int: Number of durable dirty marks so far.

    Examples:
      >>> SyncTimedbJobStore("")._persist_gen
      0
    """
    return self._writer.generation

  def _start_persist_writer(self) -> None:
    """
    Start the single daemon thread that owns sidecar writes.

    Returns:
      None

    Examples:
      >>> SyncTimedbJobStore("")._start_persist_writer()
    """
    if self.archive_dir:
      self._writer.start()

  def _persist_write(self, force: bool) -> None:
    """
    Copy the durable shadow under the store lock, then write off-lock.

    Args:
      force (bool): Unused; the writer owns interval and force policy.

    Returns:
      None

    Raises:
      OSError: When ``save_persistence_document`` fails.

    Examples:
      >>> SyncTimedbJobStore("")._persist_write(True)
    """
    del force
    with self._lock:
      if not self.archive_dir:
        return
      queued = self._queued_identities_locked()
      live = queued | self._inflight_identities_locked()
      self._prune_orphan_payloads_locked(active=live)
      self._prune_shadow_payloads_locked(queued=queued)
      path = artifact_path(self.archive_dir, JOB_STORE_SNAPSHOT_KIND)
      ingest_c = dict(self._shadow_ingest)
      lists_c = {
        kind: list(items) for kind, items in self._shadow_lists.items()
      }
      pending_c = {
        kind: list(idents) for kind, idents in self._shadow_pending.items()
      }
      payloads_src = {
        key: dict(fields) for key, fields in self._shadow_payloads.items()
      }
    payload = {
      "ingest": ingest_c,
      "lists": lists_c,
      "pending": {kind: sorted(idents) for kind, idents in pending_c.items()},
      "payloads": {
        f"{kind}|{ident}": fields
        for (kind, ident), fields in payloads_src.items()
      },
    }
    save_persistence_document(path, JOB_STORE_SNAPSHOT_KIND, payload)

  def load(self) -> None:
    """
    Replace durable queues from the snapshot sidecar when it exists.

    Returns:
      None

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").load()
    """
    if not self.archive_dir:
      return
    path = artifact_path(self.archive_dir, JOB_STORE_SNAPSHOT_KIND)
    raw = load_persistence_document(
      path,
      JOB_STORE_SNAPSHOT_KIND,
      default={},
    )
    if not isinstance(raw, dict):
      return
    ingest = raw.get("ingest") or {}
    lists = raw.get("lists") or {}
    pending = raw.get("pending") or {}
    payloads = raw.get("payloads") or {}
    ingest_built = {str(ident): float(score) for ident, score in ingest.items()}
    lists_built = {
      kind: deque(str(item) for item in (lists.get(kind) or []))
      for kind in JOB_KINDS_LIST
    }
    pending_built = {
      kind: {str(item) for item in (pending.get(kind) or [])}
      for kind in JOB_KINDS_LIST
    }
    payloads_built: dict[tuple[str, str], dict[str, str]] = {}
    if isinstance(payloads, dict):
      for key, fields in payloads.items():
        if not isinstance(fields, dict) or "|" not in str(key):
          continue
        kind, ident = str(key).split("|", 1)
        payloads_built[(kind, ident)] = {
          str(name): str(value) for name, value in fields.items()
        }
    with self._lock:
      self._ingest = ingest_built
      self._ingest_rebuild_heaps_locked()
      for kind in JOB_KINDS_LIST:
        self._lists[kind] = lists_built[kind]
        self._pending[kind] = pending_built[kind]
      self._payloads = payloads_built
      for kind in JOB_KINDS_ALL:
        self._inflight[kind] = {}
      self._leases.clear()
      self._prune_orphan_payloads_locked(
        active=self._queued_identities_locked(),
      )
      self._rebuild_shadow_locked()
      self._rebuild_ingest_band_counts_locked()
      self._dirty = False

  def _ingest_heap_for_band_locked(
    self,
    band: str,
  ) -> list[tuple[float, str]]:
    """
    Return the mutable lazy heap for one ingest score band.

    Args:
      band (str): ``hot`` or ``catchup``.

    Returns:
      list[tuple[float, str]]: Min-heap of ``(score, identity)``.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_heap_for_band_locked("hot") == []
      True
    """
    if str(band) == "hot":
      return self._ingest_heap_hot
    return self._ingest_heap_catchup

  def _ingest_heap_push_locked(self, identity: str, score: float) -> None:
    """
    Push one live ingest score onto the band heap (lazy duplicates ok).

    Args:
      identity (str): Queued ingest identity.
      score (float): Current score from ``_ingest``.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_heap_push_locked("/a", 1.0) is None
      True
    """
    scored = float(score)
    band = decode_ingest_band(scored)
    heapq.heappush(
      self._ingest_heap_for_band_locked(band), (scored, str(identity))
    )
    if band == "catchup":
      heap = self._ingest_heap_catchup_max
      if len(heap) > 2 * len(self._ingest) + 1024:  # drop stale entries
        heap[:] = [
          (-s, i)
          for i, s in self._ingest.items()
          if decode_ingest_band(s) == "catchup"
        ]
        heapq.heapify(heap)
      heapq.heappush(heap, (-scored, str(identity)))

  def _ingest_set_locked(self, identity: str, score: float) -> None:
    """
    Record a live ingest score and arm the matching band heap.

    Args:
      identity (str): Queued ingest identity.
      score (float): Band-encoded score.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_set_locked("/a", 1.0) is None
      True
    """
    ident = str(identity)
    scored = float(score)
    old = self._ingest.get(ident)
    if old is not None:
      self._ingest_band_dec_locked(float(old))
    self._ingest[ident] = scored
    self._ingest_band_inc_locked(scored)
    self._ingest_heap_push_locked(ident, scored)
    self._shadow_ingest[ident] = scored
    self._shadow_sync_payload_locked(JOB_KIND_INGEST, ident)

  def _ingest_pop_locked(self, identity: str) -> float | None:
    """
    Remove one queued ingest identity from live maps and durable shadow.

    Args:
      identity (str): Queued ingest identity.

    Returns:
      float | None: Prior score, or None when absent.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_pop_locked("/missing") is None
      True
    """
    ident = str(identity)
    old = self._ingest.pop(ident, None)
    if old is None:
      return None
    self._ingest_band_dec_locked(float(old))
    self._shadow_ingest.pop(ident, None)
    self._shadow_payloads.pop((JOB_KIND_INGEST, ident), None)
    return float(old)

  def _ingest_band_inc_locked(self, score: float) -> None:
    """
    Increment the O(1) queued counter for one score's band.

    Args:
      score (float): Band-encoded ingest score.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_band_inc_locked(1.0)
    """
    if decode_ingest_band(score) == "hot":
      self._ingest_hot_n += 1
    else:
      self._ingest_catchup_n += 1

  def _ingest_band_dec_locked(self, score: float) -> None:
    """
    Decrement the O(1) queued counter for one score's band.

    Args:
      score (float): Band-encoded ingest score.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_band_dec_locked(1.0)
    """
    if decode_ingest_band(score) == "hot":
      self._ingest_hot_n = max(0, self._ingest_hot_n - 1)
    else:
      self._ingest_catchup_n = max(0, self._ingest_catchup_n - 1)

  def _rebuild_ingest_band_counts_locked(self) -> None:
    """
    Recompute hot/catchup queued counters from the live ingest map.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._rebuild_ingest_band_counts_locked()
    """
    hot_n = 0
    catch_n = 0
    for score in self._ingest.values():
      if decode_ingest_band(score) == "hot":
        hot_n += 1
      else:
        catch_n += 1
    self._ingest_hot_n = hot_n
    self._ingest_catchup_n = catch_n

  def _rebuild_shadow_locked(self) -> None:
    """
    Rebuild incremental durable shadow maps from live queued state.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._rebuild_shadow_locked()
    """
    self._shadow_ingest = dict(self._ingest)
    self._shadow_lists = {
      kind: list(items) for kind, items in self._lists.items()
    }
    self._shadow_pending = {
      kind: set(idents) for kind, idents in self._pending.items()
    }
    queued = self._queued_identities_locked()
    self._shadow_payloads = {
      key: dict(fields)
      for key, fields in self._payloads.items()
      if key in queued
    }

  def _identity_is_queued_locked(self, kind: str, identity: str) -> bool:
    """
    Return True when ``identity`` sits on a durable queue for ``kind``.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      bool: True when queued (not merely in-flight).

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty")._identity_is_queued_locked(
      ...   "ingest",
      ...   "/a",
      ... )
      False
    """
    ident = str(identity)
    if str(kind) == JOB_KIND_INGEST:
      return ident in self._ingest
    pending = self._pending.get(str(kind))
    return bool(pending is not None and ident in pending)

  def _shadow_sync_payload_locked(self, kind: str, identity: str) -> None:
    """
    Mirror one queued payload row into the durable shadow (or drop it).

    Args:
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._shadow_sync_payload_locked("ingest", "/a")
    """
    key = (str(kind), str(identity))
    fields = self._payloads.get(key)
    if self._identity_is_queued_locked(kind, identity) and fields is not None:
      self._shadow_payloads[key] = dict(fields)
    else:
      self._shadow_payloads.pop(key, None)

  def _prune_shadow_payloads_locked(
    self,
    *,
    queued: set[tuple[str, str]],
  ) -> None:
    """
    Drop shadow payload rows that are no longer on durable queues.

    Args:
      queued (set[tuple[str, str]]): Current durable queue identities.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._prune_shadow_payloads_locked(queued=set())
    """
    for key in list(self._shadow_payloads):
      if key not in queued:
        del self._shadow_payloads[key]

  def _ingest_rebuild_heaps_locked(self) -> None:
    """
    Rebuild both band heaps from the live ``_ingest`` map.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._ingest_rebuild_heaps_locked() is None
      True
    """
    self._ingest_heap_hot = []
    self._ingest_heap_catchup = []
    self._ingest_heap_catchup_max = []
    for ident, score in self._ingest.items():
      self._ingest_heap_push_locked(ident, float(score))

  def _queued_identities_locked(self) -> set[tuple[str, str]]:
    """
    Return (kind, identity) pairs currently sitting on durable queues.

    In-flight claims are omitted so crash snapshots cannot resurrect
    payload metadata for work the reconstruct path must refill.

    Returns:
      set[tuple[str, str]]: Queued ingest and LIST identities.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._queued_identities_locked()
      set()
    """
    active: set[tuple[str, str]] = {
      (JOB_KIND_INGEST, ident) for ident in self._ingest
    }
    for kind in JOB_KINDS_LIST:
      for ident in self._lists[kind]:
        active.add((kind, ident))
    return active

  def _inflight_identities_locked(self) -> set[tuple[str, str]]:
    """
    Return (kind, identity) pairs currently claimed in memory.

    Live persist must keep fingerprint/attempt rows for these identities
    so a later requeue can still bump attempt.

    Returns:
      set[tuple[str, str]]: In-flight identities across all job kinds.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._inflight_identities_locked()
      set()
    """
    active: set[tuple[str, str]] = set()
    for kind in JOB_KINDS_ALL:
      for ident in self._inflight[kind]:
        active.add((kind, ident))
    return active

  def _prune_orphan_payloads_locked(
    self,
    *,
    active: set[tuple[str, str]],
  ) -> None:
    """
    Drop fingerprint/attempt rows that no longer have queued work.

    Args:
      active (set[tuple[str, str]]): Identities that may keep payload
        metadata.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._prune_orphan_payloads_locked(active=set())
    """
    for key in list(self._payloads):
      if key not in active:
        del self._payloads[key]

  def _mark_dirty_locked(self) -> None:
    """
    Remember that durable queues changed and may need a snapshot.

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._mark_dirty_locked()
    """
    self._writer.mark_dirty()

  def _maybe_persist_locked(self) -> None:
    """
    Notify the persist writer after a durable mutation (never write here).

    Returns:
      None

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._maybe_persist_locked()
    """
    if not self._dirty:
      return
    self.persist(force=False)

  def zadd_ingest(
    self,
    identity: str,
    score: float,
    fingerprint: str | None = None,
    *,
    over_cap: bool = False,
  ) -> int:
    """
    Queue or reband one ingest identity.

    New catchup identities are refused at the discover ingest cap
    (``sync_ingest_queue_max_size``) unless ``over_cap``. New hot-band
    identities still join so live listend files are not starved behind a
    full catchup.

    Args:
      identity (str): Normalized ingest path.
      score (float): Band-encoded score.
      fingerprint (str | None): Optional size/mtime payload.
      over_cap (bool): Admit past the discover cap. Day-close handoff and
        dead-letter readmit set it: their volume is bounded by the closing
        day's remaining raw / the migration batch, and a refusal there is
        a silent drop that leaves the day open forever.

    Returns:
      int: 1 when the identity is newly queued, else 0.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.zadd_ingest("/raw/a", 1.0)
      1
    """
    from hpcperfstats.dbload.lib.sync_timedb_job_discover import (
      discover_ingest_capacity_limit,
    )

    ident = str(identity)
    cap = discover_ingest_capacity_limit()
    with self._lock:
      if ident in self._inflight[JOB_KIND_INGEST]:
        return 0
      existed = ident in self._ingest
      if (
        not existed
        and not over_cap
        and not self._has_capacity_locked(
          JOB_KIND_INGEST,
          cap,
        )
        and decode_ingest_band(score) != "hot"
      ):
        return 0
      self._ingest_set_locked(ident, float(score))
      if fingerprint:
        self._payloads.setdefault(
          (JOB_KIND_INGEST, ident),
          {},
        )["fingerprint"] = str(fingerprint)
        self._shadow_sync_payload_locked(JOB_KIND_INGEST, ident)
      self._mark_dirty_locked()
    self._maybe_persist_locked()
    return 0 if existed else 1

  def evict_youngest_catchup_ingest(self, older_day: date) -> str | None:
    """
    Remove the youngest queued backfill file when it is younger than a day.

    Discover calls this at the ingest cap so an older backfill file takes
    the place of the youngest queued one (oldest-first admission). Hot-band
    and in-flight work is never removed; the walk that called this reaches
    the removed file again later because its input is sorted oldest first.

    Args:
      older_day (date): Calendar day of the file waiting for room.

    Returns:
      str | None: Removed identity, or None when nothing queued is younger.

    Examples:
      >>> store = SyncTimedbJobStore("")
      >>> store.evict_youngest_catchup_ingest(date(2026, 8, 1)) is None
      True
    """
    with self._lock:
      heap = self._ingest_heap_catchup_max
      while heap:
        neg_score, ident = heap[0]
        if self._ingest.get(ident) != -neg_score:
          heapq.heappop(heap)  # stale: claimed, removed or re-scored
          continue
        day = decode_catchup_calendar_day(-neg_score)
        if day is None or day <= older_day:
          return None
        heapq.heappop(heap)
        self._ingest_pop_locked(ident)
        self._payloads.pop((JOB_KIND_INGEST, ident), None)
        self._mark_dirty_locked()
        break
      else:
        return None
    self._maybe_persist_locked()
    return ident

  def enqueue_list(
    self,
    kind: str,
    identity: str,
    *,
    dedupe: bool = False,
  ) -> int:
    """
    Append one LIST-kind identity, optionally skipping duplicates.

    Args:
      kind (str): discover, append, or day_close.
      identity (str): Job identity.
      dedupe (bool): Skip when already queued or in flight.

    Returns:
      int: Queue depth after the push, or 0 when skipped/capped.

    Raises:
      ValueError: When kind is not a LIST kind.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.enqueue_list("append", "/tar/a")
      1
    """
    if str(kind) not in JOB_KINDS_LIST:
      raise ValueError(f"kind {kind!r} is not a LIST queue kind")
    ident = str(identity)
    cap = queue_capacity_limit()
    with self._lock:
      if dedupe and (
        ident in self._pending[kind] or ident in self._inflight[kind]
      ):
        return 0
      if not self._has_capacity_locked(kind, cap):
        return 0
      self._lists[kind].append(ident)
      self._pending[kind].add(ident)
      self._shadow_lists[kind].append(ident)
      self._shadow_pending[kind].add(ident)
      self._shadow_sync_payload_locked(kind, ident)
      self._mark_dirty_locked()
      depth = len(self._lists[kind])
    self._maybe_persist_locked()
    return depth

  def claim_ingest(
    self,
    *,
    band: str,
    owner_token: str,
    ttl_s: int | None = None,
    now_s: float | None = None,
    max_n: int = 1,
  ) -> list[ClaimedJob]:
    """
    Claim up to max_n ingest jobs from one score band.

    Args:
      band (str): hot or catchup.
      owner_token (str): Owner token from make_lease_owner_token.
      ttl_s (int | None): Claim deadline override in seconds.
      now_s (float | None): Clock override for tests.
      max_n (int): Maximum claims this call.

    Returns:
      list[ClaimedJob]: Zero or more claims.

    Raises:
      ValueError: When owner_token is empty or max_n is less than 1.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.claim_ingest(band="hot", owner_token="n:h:b:1")
      []
    """
    if not owner_token:
      raise ValueError("owner_token is required to claim a job")
    want = int(max_n)
    if want < 1:
      raise ValueError("max_n must be >= 1")
    lo, hi = ingest_score_range(band)
    ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
    now = time.time() if now_s is None else float(now_s)
    deadline = now + ttl
    claimed_raw: list[tuple[str, str, str, float, float, str]] = []
    with self._lock:
      heap = self._ingest_heap_for_band_locked(band)
      while len(claimed_raw) < want and heap:
        snap_score, ident = heapq.heappop(heap)
        if ident in self._inflight[JOB_KIND_INGEST]:
          continue
        live = self._ingest.get(ident)
        if live is None:
          continue
        if float(live) != float(snap_score):
          # Stale heap entry after reband/score bump; re-arm live.
          self._ingest_heap_push_locked(ident, float(live))
          continue
        if not (lo <= float(live) <= hi):
          self._ingest_heap_push_locked(ident, float(live))
          continue
        self._ingest_pop_locked(ident)
        self._inflight[JOB_KIND_INGEST][ident] = (
          deadline,
          owner_token,
          float(live),
        )
        self._leases[(JOB_KIND_INGEST, ident)] = owner_token
        fields = self._payloads.get((JOB_KIND_INGEST, ident), {})
        claimed_raw.append(
          (
            JOB_KIND_INGEST,
            ident,
            owner_token,
            deadline,
            float(live),
            str(fields.get("fingerprint") or ""),
            str(fields.get("emit") or ""),
          ),
        )
      if claimed_raw:
        self._mark_dirty_locked()
    if claimed_raw:
      self._maybe_persist_locked()
    return [
      ClaimedJob(
        kind=kind,
        identity=ident,
        owner_token=token,
        deadline=dead,
        score=score,
        fingerprint=fp,
        emit=emit,
      )
      for kind, ident, token, dead, score, fp, emit in claimed_raw
    ]

  def claim_list(
    self,
    *,
    kind: str,
    owner_token: str,
    ttl_s: int | None = None,
    now_s: float | None = None,
  ) -> ClaimedJob | None:
    """
    Claim the oldest queued LIST identity.

    Args:
      kind (str): discover, append, or day_close.
      owner_token (str): Owner token from make_lease_owner_token.
      ttl_s (int | None): Claim deadline override in seconds.
      now_s (float | None): Clock override for tests.

    Returns:
      ClaimedJob | None: Claim, or None when the queue is empty.

    Raises:
      ValueError: When kind is not a LIST kind or owner_token is empty.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.claim_list(kind="append", owner_token="n:h:b:1") is None
      True
    """
    if str(kind) not in JOB_KINDS_LIST:
      raise ValueError(f"kind {kind!r} is not a LIST queue kind")
    if not owner_token:
      raise ValueError("owner_token is required to claim a job")
    ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
    now = time.time() if now_s is None else float(now_s)
    deadline = now + ttl
    claimed_raw: tuple[str, str, str, float] | None = None
    with self._lock:
      while self._lists[kind]:
        ident = self._lists[kind].popleft()
        self._pending[kind].discard(ident)
        shadow_list = self._shadow_lists[kind]
        if shadow_list and shadow_list[0] == ident:
          shadow_list.pop(0)
        elif ident in shadow_list:
          shadow_list.remove(ident)
        self._shadow_pending[kind].discard(ident)
        self._shadow_payloads.pop((str(kind), ident), None)
        if ident in self._inflight[kind]:
          continue
        self._inflight[kind][ident] = (deadline, owner_token, None)
        self._leases[(kind, ident)] = owner_token
        self._mark_dirty_locked()
        claimed_raw = (str(kind), ident, owner_token, deadline)
        break
    if claimed_raw is not None:
      self._maybe_persist_locked()
      kind_s, ident, token, dead = claimed_raw
      return ClaimedJob(
        kind=kind_s,
        identity=ident,
        owner_token=token,
        deadline=dead,
        score=None,
      )
    return None

  def ack(self, *, kind: str, identity: str, owner_token: str) -> bool:
    """
    Drop a claimed job when the caller still owns it.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.
      owner_token (str): Owner token from the claim.

    Returns:
      bool: True when this owner cleared its own in-flight entry.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.ack(kind="ingest", identity="/a", owner_token="n:h:b:1")
      False
    """
    if not owner_token:
      return False
    ident = str(identity)
    with self._lock:
      entry = self._inflight.get(kind, {}).get(ident)
      if entry is None or entry[1] != owner_token:
        return False
      self._inflight[kind].pop(ident, None)
      self._leases.pop((kind, ident), None)
      self._payloads.pop((kind, ident), None)
      if kind in self._pending:
        self._pending[kind].discard(ident)
      # Inflight-only: durable queues already dropped this identity at claim.
    return True

  def requeue(
    self,
    *,
    kind: str,
    identity: str,
    owner_token: str,
    score: float | int | None = None,
  ) -> bool:
    """
    Return a claimed job to its queue when the caller still owns it.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.
      owner_token (str): Owner token from the claim.
      score (float | int | None): Ingest score to restore; ignored for
        LIST kinds and defaulted to CATCHUP_SCORE_BASE when omitted.

    Returns:
      bool: True when this owner requeued its own claim.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.requeue(kind="append", identity="d", owner_token="n:h:b:1")
      False
    """
    if not owner_token:
      return False
    ident = str(identity)
    with self._lock:
      entry = self._inflight.get(kind, {}).get(ident)
      if entry is None or entry[1] != owner_token:
        return False
      self._inflight[kind].pop(ident, None)
      self._leases.pop((kind, ident), None)
      if str(kind) == JOB_KIND_INGEST:
        restore = CATCHUP_SCORE_BASE if score is None else float(score)
        self._ingest_set_locked(ident, restore)
        self._shadow_sync_payload_locked(kind, ident)
      else:
        self._lists[kind].append(ident)
        self._pending[kind].add(ident)
        self._shadow_lists[kind].append(ident)
        self._shadow_pending[kind].add(ident)
        self._shadow_sync_payload_locked(kind, ident)
      self._mark_dirty_locked()
    self._maybe_persist_locked()
    return True

  def _has_capacity_locked(self, kind: str, cap: int) -> bool:
    """
    Return True when another durable member may be queued.

    Args:
      kind (str): Job kind.
      cap (int): Capacity bound from ``queue_capacity_limit()``, read
        before the store RLock.

    Returns:
      bool: True when the queue is below the configured cap.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store._has_capacity_locked("ingest", queue_capacity_limit())
      True
    """
    if str(kind) == JOB_KIND_INGEST:
      return len(self._ingest) < cap
    return len(self._lists[kind]) < cap

  def queued_count(self, kind: str) -> int:
    """
    Return queued (not in-flight) depth for a job kind.

    Args:
      kind (str): Job kind (``ingest`` or a LIST kind).

    Returns:
      int: Queued member count, or 0 for unknown kinds.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").queued_count("ingest")
      0
    """
    with self._lock:
      if str(kind) == JOB_KIND_INGEST:
        return len(self._ingest)
      if kind in self._lists:
        return len(self._lists[kind])
      return 0

  def ingest_count_in_score_range(self, lo: Any, hi: Any) -> int:
    """
    Count queued ingest members whose scores fall in an inclusive range.

    Args:
      lo (Any): Inclusive lower bound, including -inf.
      hi (Any): Inclusive upper bound, including +inf.

    Returns:
      int: Matching member count.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").ingest_count_in_score_range(0, 1)
      0
    """
    low = _bound_to_float(lo, default=float("-inf"))
    high = _bound_to_float(hi, default=float("+inf"))
    hot_lo, hot_hi = ingest_score_range("hot")
    catch_lo, catch_hi = ingest_score_range("catchup")
    with self._lock:
      if low == hot_lo and high == hot_hi:
        return int(self._ingest_hot_n)
      if low == catch_lo and high == catch_hi:
        return int(self._ingest_catchup_n)
      scores = list(self._ingest.values())
    return sum(1 for score in scores if low <= score <= high)

  def ingest_score(self, identity: str) -> float | None:
    """
    Return the queued ingest score for one identity.

    Args:
      identity (str): Ingest identity.

    Returns:
      float | None: Score, or None when the identity is not queued.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").ingest_score("/a") is None
      True
    """
    with self._lock:
      return self._ingest.get(str(identity))

  def list_slice(self, kind: str, start: int, end: int) -> list[str]:
    """
    Return a slice of a LIST queue, matching inclusive LIST indexes.

    Args:
      kind (str): LIST job kind.
      start (int): Inclusive start index.
      end (int): Inclusive end index; -1 means through the tail.

    Returns:
      list[str]: Identities in the requested slice.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").list_slice("append", 0, -1)
      []
    """
    with self._lock:
      dq = self._lists.get(kind)
      if not dq:
        return []
      n = len(dq)
      start_i = int(start)
      end_i = int(end)
      if end_i == -1:
        end_i = n - 1
      if start_i < 0:
        start_i = max(0, n + start_i)
      if end_i < 0:
        end_i = max(-1, n + end_i)
      if start_i > end_i or start_i >= n:
        return []
      # Critical section is copy-only; clamps need ``n`` under the lock.
      return list(islice(dq, start_i, end_i + 1))

  def reorder_list(self, kind: str, ordered_idents: list[str]) -> bool:
    """
    Replace a LIST queue order when ``ordered_idents`` matches the current
    multiset of identities (used to prefer oldest day_close first).

    Args:
      kind (str): Job kind (for example ``JOB_KIND_DAY_CLOSE``).
      ordered_idents (List[str]): Desired front-to-back identity order.

    Returns:
      bool: True when the list was rewritten; False when kind is unknown,
      lengths differ, or membership differs.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> store.reorder_list("day_close", [])
      False
    """
    if kind not in self._lists:
      return False
    desired = list(ordered_idents)
    with self._lock:
      current = list(self._lists[kind])
    if len(current) != len(desired):
      return False
    if sorted(current) != sorted(desired):
      return False
    if current == desired:
      return False
    with self._lock:
      if list(self._lists[kind]) != current:
        return False
      self._lists[kind] = deque(desired)
      self._shadow_lists[kind] = list(desired)
      self._mark_dirty_locked()
    self._maybe_persist_locked()
    return True

  def lease_token(self, kind: str, identity: str) -> str | None:
    """
    Return the owner token for a claimed identity, if a lease is present.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      str | None: Owner token, or None when the lease is absent.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").lease_token("ingest", "/a") is None
      True
    """
    with self._lock:
      return self._leases.get((str(kind), str(identity)))

  def inflight_contains(self, kind: str, identity: str) -> bool:
    """
    Return True when an identity is currently in flight for a kind.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      bool: True when the identity is in flight.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").inflight_contains("ingest", "a")
      False
    """
    with self._lock:
      return str(identity) in self._inflight.get(str(kind), {})

  def extend_inflight_deadline(
    self,
    *,
    kind: str,
    identity: str,
    extend_s: float = 600.0,
  ) -> bool:
    """
    Push one in-flight deadline forward without a lease heartbeat renew.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.
      extend_s (float): Seconds added to ``time.time()`` for the new
        deadline.

    Returns:
      bool: True when the identity was in flight and rewritten.

    Examples:
      >>> SyncTimedbJobStore("/tmp/empty").extend_inflight_deadline(
      ...   kind="ingest",
      ...   identity="a",
      ... )
      False
    """
    ident = str(identity)
    deadline = time.time() + float(extend_s)
    with self._lock:
      entry = self._inflight.get(str(kind), {}).get(ident)
      if entry is None:
        return False
      _old, owner, score = entry
      self._inflight[str(kind)][ident] = (deadline, owner, score)
      return True


def _bound_to_float(value: Any, *, default: float) -> float:
  """
  Convert a score bound to a float.

  Args:
    value (Any): Numeric bound, "-inf", or "+inf".
    default (float): Fallback when the value is empty.

  Returns:
    float: Inclusive numeric bound.

  Examples:
    >>> _bound_to_float("-inf", default=0.0)
    -inf
  """
  if value in (None, ""):
    return default
  text = str(value)
  if text == "-inf":
    return float("-inf")
  if text == "+inf":
    return float("+inf")
  return float(value)


def ingest_score_range(band: str) -> tuple[float, float]:
  """
  Return inclusive min and max scores for a ranged ingest claim.

  Args:
    band (str): hot or catchup.

  Returns:
    tuple[float, float]: Score window.

  Raises:
    ValueError: When band is unknown.

  Examples:
    >>> ingest_score_range("hot")[0] == float("-inf")
    True
  """
  text = str(band or "").strip().lower()
  if text == "hot":
    return (float("-inf"), float(CATCHUP_SCORE_BASE) - 1.0)
  if text == "catchup":
    return (float(CATCHUP_SCORE_BASE), float("+inf"))
  raise ValueError(f"band must be 'hot' or 'catchup', got {band!r}")


def _tie_break_from_identity(identity: str) -> int:
  """
  Map an identity into [0, SCORE_STRIDE) for same-day ordering.

  Args:
    identity (str): Ingest identity.

  Returns:
    int: Non-negative tie-break strictly less than SCORE_STRIDE.

  Examples:
    >>> 0 <= _tie_break_from_identity("x") < SCORE_STRIDE
    True
  """
  digest = hashlib.sha1(  # noqa: S324 — non-crypto score tie-break only
    str(identity).encode("utf-8")
  ).digest()
  return int.from_bytes(digest[:4], "big") % SCORE_STRIDE


def encode_ingest_score(
  *,
  band: str,
  day: date,
  today: date,
  identity: str,
) -> int:
  """
  Encode hot/catchup band and calendar day into an ingest score.

  Args:
    band (str): hot or catchup.
    day (date): Calendar day of the raw file.
    today (date): Local today used for hot-window math.
    identity (str): Ingest identity (feeds same-day tie-break).

  Returns:
    int: Queue score.

  Raises:
    ValueError: When band is not hot or catchup.

  Examples:
    >>> d = date(2026, 8, 20)
    >>> encode_ingest_score(
    ...   band="hot", day=d, today=d, identity="a"
    ... ) < CATCHUP_SCORE_BASE
    True
  """
  tie = _tie_break_from_identity(identity)
  day_ord = int(day.toordinal())
  today_ord = int(today.toordinal())
  text = str(band or "").strip().lower()
  if text == "hot":
    raw = int(HOT_SCORE_BASE + (today_ord - day_ord) * SCORE_STRIDE + tie)
    return max(int(HOT_SCORE_BASE), raw)
  if text == "catchup":
    return int(CATCHUP_SCORE_BASE + day_ord * SCORE_STRIDE + tie)
  raise ValueError(f"band must be 'hot' or 'catchup', got {band!r}")


def penalize_ingest_score(score: float | int | None) -> float:
  """
  Defer a queued ingest job behind its same-day peers, never past its day.

  Every deprioritizing requeue (fill backpressure skip, reband, expired
  lease) goes through here. The bump is clamped below the next day's first
  score, so no number of requeues can reorder days: the persisted score of a
  day-D file always sorts before any day-D+1 file (oldest-day-first order).

  Args:
    score (float | int | None): Current score; ``None`` starts at the
      catchup base.

  Returns:
    float: ``score + INGEST_PENALTY_STEP``, at most the last score of the
    same ``SCORE_STRIDE`` day slot.

  Examples:
    >>> day = CATCHUP_SCORE_BASE + 5 * SCORE_STRIDE
    >>> penalize_ingest_score(day) == day + INGEST_PENALTY_STEP
    True
    >>> penalize_ingest_score(day + SCORE_STRIDE - 1) == day + SCORE_STRIDE - 1
    True
  """
  value = int(CATCHUP_SCORE_BASE if score is None else score)
  last_in_day = value - value % SCORE_STRIDE + SCORE_STRIDE - 1
  return float(min(value + INGEST_PENALTY_STEP, last_in_day))


def job_lease_ttl_seconds() -> int:
  """
  Return claim deadline seconds for in-memory job ownership.

  Returns:
    int: Positive deadline seconds (at least JOB_LEASE_TTL_FLOOR_S).

  Examples:
    >>> job_lease_ttl_seconds() >= JOB_LEASE_TTL_FLOOR_S
    True
  """
  try:
    raw = int(cfg.get_sync_ingest_per_file_timeout_max_s())
  except Exception:
    raw = 86400
  if raw < JOB_LEASE_TTL_FLOOR_S:
    return JOB_LEASE_TTL_FLOOR_S
  return int(raw)


def queue_capacity_limit() -> int:
  """
  Return the maximum member count allowed per durable queue.

  Returns:
    int: Positive capacity bound.

  Examples:
    >>> queue_capacity_limit() >= QUEUE_MAX_MEMBERS_FLOOR
    True
  """
  try:
    raw = int(cfg.get_sync_job_queue_max_members())
  except Exception:
    raw = 2_000_000
  return max(QUEUE_MAX_MEMBERS_FLOOR, raw)


def current_boot_id() -> str:
  """
  Return a stable per-boot identifier for this host.

  Returns:
    str: Short boot identifier (unknown when unavailable).

  Examples:
    >>> isinstance(current_boot_id(), str)
    True
  """
  global _BOOT_ID_CACHE
  if _BOOT_ID_CACHE is not None:
    return _BOOT_ID_CACHE
  boot = ""
  try:
    with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as fh:
      boot = fh.read().strip().replace("-", "")[:16]
  except OSError:
    boot = ""
  if not boot:
    try:
      boot = str(int(time.time() - time.monotonic()))
    except OSError, ValueError, OverflowError:
      boot = "unknown"
  _BOOT_ID_CACHE = str(boot).replace(":", "_").replace("|", "_") or "unknown"
  return _BOOT_ID_CACHE


def make_lease_owner_token(
  *,
  pid: int | None = None,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> str:
  """
  Build an owner token {nonce}:{host}:{boot}:{pid}.

  Args:
    pid (int | None): Owner PID; defaults to os.getpid().
    hostname (str | None): Owner hostname; defaults to the local hostname.
    boot_id (str | None): Owner boot id; defaults to current_boot_id().

  Returns:
    str: Owner token stored with the in-memory claim.

  Examples:
    >>> tok = make_lease_owner_token(pid=42, hostname="h", boot_id="b")
    >>> tok.endswith(":h:b:42")
    True
  """
  owner_pid = int(os.getpid() if pid is None else pid)
  if hostname is None:
    try:
      host = socket.gethostname()
    except OSError:
      host = "unknown"
  else:
    host = hostname
  boot = current_boot_id() if boot_id is None else boot_id
  return f"{secrets.token_hex(16)}:{str(host or 'unknown').replace(':', '_').replace('|', '_')}:{str(boot or 'unknown').replace(':', '_').replace('|', '_')}:{owner_pid}"


def zadd_ingest_job(
  store: SyncTimedbJobStore,
  *,
  identity: str,
  score: float | int,
  fingerprint: str | None = None,
  over_cap: bool = False,
) -> int:
  """
  Queue an ingest identity onto the in-process ingest map.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    identity (str): Ingest member identity.
    score (float | int): Band-encoded score.
    fingerprint (str | None): Optional size/mtime payload.
    over_cap (bool): Admit past the discover ingest cap (handoff, readmit).

  Returns:
    int: 1 when newly queued, else 0.

  Examples:
    >>> store = SyncTimedbJobStore("/tmp/empty")
    >>> zadd_ingest_job(store, identity="/a", score=0)
    1
  """
  return store.zadd_ingest(
    str(identity), float(score), fingerprint, over_cap=over_cap
  )


def enqueue_list_job(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  dedupe: bool = False,
) -> int:
  """
  Queue a discover, append, or day_close identity.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): LIST job kind.
    identity (str): Job identity.
    dedupe (bool): Skip when already queued or in flight.

  Returns:
    int: Queue depth after push, or 0 when skipped.

  Examples:
    >>> store = SyncTimedbJobStore("/tmp/empty")
    >>> enqueue_list_job(store, kind="append", identity="p")
    1
  """
  return store.enqueue_list(kind, identity, dedupe=dedupe)


def claim_ingest_job(
  store: SyncTimedbJobStore,
  *,
  band: str,
  owner_token: str,
  ttl_s: int | None = None,
  now_s: float | None = None,
  probe_depth: int | None = None,
) -> ClaimedJob | None:
  """
  Claim one ingest job from a score band.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    band (str): hot or catchup.
    owner_token (str): Token from make_lease_owner_token.
    ttl_s (int | None): Deadline override.
    now_s (float | None): Clock override for tests.
    probe_depth (int | None): Unused leftover from the probe loop.

  Returns:
    ClaimedJob | None: Claim, or None when the band has no free work.

  Examples:
    >>> store = SyncTimedbJobStore("/tmp/empty")
    >>> claim_ingest_job(store, band="hot", owner_token="n:h:b:1") is None
    True
  """
  del probe_depth
  jobs = store.claim_ingest(
    band=band,
    owner_token=owner_token,
    ttl_s=ttl_s,
    now_s=now_s,
    max_n=1,
  )
  return jobs[0] if jobs else None


def claim_list_job(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  owner_token: str,
  ttl_s: int | None = None,
  now_s: float | None = None,
) -> ClaimedJob | None:
  """
  Claim one LIST job.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): LIST job kind.
    owner_token (str): Token from make_lease_owner_token.
    ttl_s (int | None): Deadline override.
    now_s (float | None): Clock override for tests.

  Returns:
    ClaimedJob | None: Claim, or None when the queue is empty.

  Examples:
    >>> store = SyncTimedbJobStore("/tmp/empty")
    >>> claim_list_job(store, kind="append", owner_token="n:h:b:1") is None
    True
  """
  return store.claim_list(
    kind=kind,
    owner_token=owner_token,
    ttl_s=ttl_s,
    now_s=now_s,
  )


def ack_job(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  owner_token: str,
) -> bool:
  """
  Mark a claimed job terminal.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Owner token from the claim.

  Returns:
    bool: True when this owner cleared its own in-flight entry.

  Examples:
    >>> store = SyncTimedbJobStore("/tmp/empty")
    >>> ack_job(store, kind="append", identity="d", owner_token="n:h:b:1")
    False
  """
  return store.ack(kind=kind, identity=identity, owner_token=owner_token)


def requeue_job(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  owner_token: str,
  score: float | int | None = None,
) -> bool:
  """
  Return a claimed job to its queue after a retryable failure.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Owner token from the claim.
    score (float | int | None): Ingest score to restore.

  Returns:
    bool: True when this owner requeued its own claim.

  Examples:
    >>> store = SyncTimedbJobStore("/tmp/empty")
    >>> requeue_job(store, kind="append", identity="d", owner_token="n:h:b:1")
    False
  """
  return store.requeue(
    kind=kind,
    identity=identity,
    owner_token=owner_token,
    score=score,
  )


def decode_ingest_band(score: float | int) -> str:
  """
  Classify an ingest score as hot or catchup.

  Args:
    score (float | int): Score from encode_ingest_score.

  Returns:
    str: hot when score is below CATCHUP_SCORE_BASE, else catchup.

  Examples:
    >>> decode_ingest_band(0)
    'hot'
    >>> decode_ingest_band(CATCHUP_SCORE_BASE)
    'catchup'
  """
  if float(score) < float(CATCHUP_SCORE_BASE):
    return "hot"
  return "catchup"


def decode_catchup_calendar_day(score: float | int) -> date | None:
  """
  Recover the calendar day encoded in a catchup-band score.

  Args:
    score (float | int): Score from encode_ingest_score.

  Returns:
    date | None: Calendar day, or None when the score is hot or invalid.

  Examples:
    >>> d = date(2025, 5, 5)
    >>> s = encode_ingest_score(
    ...   band="catchup",
    ...   day=d,
    ...   today=d,
    ...   identity="a",
    ... )
    >>> decode_catchup_calendar_day(s) == d
    True
  """
  if decode_ingest_band(score) != "catchup":
    return None
  day_ord = int(
    (float(score) - float(CATCHUP_SCORE_BASE)) // float(SCORE_STRIDE),
  )
  try:
    return date.fromordinal(day_ord)
  except ValueError, OverflowError:
    return None


def ingest_identity(path: str, size: int = 0, mtime_ns: int = 0) -> str:
  """
  Build the ingest identity for a raw stats path.

  Args:
    path (str): Raw stats path, normalized with os.path.normpath.
    size (int): File size accepted for caller compatibility and ignored.
    mtime_ns (int): mtime accepted for caller compatibility and ignored.

  Returns:
    str: Normalized path used as the durable identity.

  Raises:
    ValueError: When path is empty.

  Examples:
    >>> ingest_identity("/a/../b", 10, 20)
    '/b'
  """
  del size, mtime_ns
  text = str(path or "").strip()
  if not text:
    raise ValueError("path is required for ingest identity")
  return os.path.normpath(text)


def ingest_fingerprint(size: int, mtime_ns: int) -> str:
  """
  Encode a size and mtime pair for the ingest payload.

  Args:
    size (int): File size in bytes.
    mtime_ns (int): st_mtime_ns at enqueue or re-stat time.

  Returns:
    str: size|mtime_ns fingerprint.

  Examples:
    >>> ingest_fingerprint(10, 20)
    '10|20'
  """
  return f"{int(size)}|{int(mtime_ns)}"


def write_job_fingerprint(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  fingerprint: str,
) -> None:
  """
  Store a job fingerprint on the in-memory payload map.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    fingerprint (str): Size/mtime encoding.

  Returns:
    None

  Examples:
    >>> store = SyncTimedbJobStore("")
    >>> write_job_fingerprint(
    ...   store,
    ...   kind="ingest",
    ...   identity="/a",
    ...   fingerprint="1|2",
    ... )
  """
  if not fingerprint:
    return
  with store._lock:
    store._payloads.setdefault((str(kind), str(identity)), {})[
      "fingerprint"
    ] = str(fingerprint)
    if store._identity_is_queued_locked(kind, identity):
      store._shadow_sync_payload_locked(kind, identity)
      store._mark_dirty_locked()
  store._maybe_persist_locked()


def write_job_payload_field(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  name: str,
  value: str,
) -> None:
  """
  Store one string field on a job payload.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    name (str): Payload key, such as ``emit``.
    value (str): Field text.

  Returns:
    None

  Examples:
    >>> store = SyncTimedbJobStore("")
    >>> write_job_payload_field(
    ...   store,
    ...   kind="ingest",
    ...   identity="/a",
    ...   name="emit",
    ...   value="insert",
    ... )
  """
  with store._lock:
    store._payloads.setdefault((str(kind), str(identity)), {})[str(name)] = str(
      value
    )
    if store._identity_is_queued_locked(kind, identity):
      store._shadow_sync_payload_locked(kind, identity)
    store._mark_dirty_locked()
  store._maybe_persist_locked()


def read_job_payload_field(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  name: str,
) -> str:
  """
  Return one stored payload field, or empty when unset.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    name (str): Payload key.

  Returns:
    str: Field text, or empty when unset.

  Examples:
    >>> read_job_payload_field(
    ...   SyncTimedbJobStore(""),
    ...   kind="ingest",
    ...   identity="/a",
    ...   name="emit",
    ... )
    ''
  """
  with store._lock:
    return str(
      store._payloads.get((str(kind), str(identity)), {}).get(
        str(name),
      )
      or "",
    )


def read_job_fingerprint(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
) -> str:
  """
  Return the stored fingerprint for a job identity, or empty.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    str: Fingerprint text, or empty when unset.

  Examples:
    >>> read_job_fingerprint(
    ...   SyncTimedbJobStore(""),
    ...   kind="ingest",
    ...   identity="/a",
    ... )
    ''
  """
  with store._lock:
    return str(
      store._payloads.get((str(kind), str(identity)), {}).get(
        "fingerprint",
      )
      or "",
    )


def fingerprint_matches_path(path: str, fingerprint: str) -> bool:
  """
  Return True when path's current size and mtime match fingerprint.

  Args:
    path (str): Filesystem path to stat.
    fingerprint (str): size|mtime_ns from ingest_fingerprint.

  Returns:
    bool: True when the live stat matches.

  Examples:
    >>> fingerprint_matches_path("/missing", "1|2")
    False
  """
  text = str(fingerprint or "")
  if "|" not in text or not path:
    return False
  try:
    st = os.stat(path)
  except OSError:
    return False
  return text == ingest_fingerprint(st.st_size, st.st_mtime_ns)


def ingest_band_slot_caps(pool_size: int) -> tuple[int, int]:
  """
  Compute reserved hot and catchup ingest slot caps.

  Args:
    pool_size (int): Ingest pool size; must be at least 1.

  Returns:
    tuple[int, int]: (hot_cap, catchup_cap) summing to pool_size.

  Raises:
    ValueError: When pool_size is less than 1.

  Examples:
    >>> ingest_band_slot_caps(16)
    (10, 6)
    >>> ingest_band_slot_caps(1)
    (1, 0)
  """
  pool = int(pool_size)
  if pool < 1:
    raise ValueError("pool_size must be >= 1")
  if pool == 1:
    return (1, 0)
  hot_cap = max(1, (2 * pool) // 3)
  catchup_cap = pool - hot_cap
  if catchup_cap < 1:
    catchup_cap = 1
    hot_cap = pool - 1
  return (hot_cap, catchup_cap)


def ingest_claim_probe_depth(*, hot_q: int = 0, pool: int = 1) -> int:
  """
  Return claim probe depth, elevated when the hot queue exceeds pool size.

  Args:
    hot_q (int): Hot-band queued depth.
    pool (int): Ingest pool size.

  Returns:
    int: Bounded probe depth kept for caller compatibility.

  Examples:
    >>> ingest_claim_probe_depth(hot_q=0, pool=16)
    8
    >>> ingest_claim_probe_depth(hot_q=500, pool=16) > 8
    True
  """
  base = 8
  if int(hot_q or 0) <= int(pool or 1):
    return base
  elevated = max(base, int(hot_q) // max(1, int(pool)))
  return min(64, elevated)


def claim_ingest_jobs(
  store: SyncTimedbJobStore,
  *,
  band: str,
  owner_token: str,
  max_n: int,
  ttl_s: int | None = None,
  now_s: float | None = None,
  probe_depth: int | None = None,
) -> list[ClaimedJob]:
  """
  Claim up to max_n ingest jobs from one score band.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    band (str): hot or catchup.
    owner_token (str): Token from make_lease_owner_token.
    max_n (int): Maximum claims this call.
    ttl_s (int | None): Deadline override.
    now_s (float | None): Clock override for tests.
    probe_depth (int | None): Unused leftover from the probe loop.

  Returns:
    list[ClaimedJob]: Zero or more claims.

  Examples:
    >>> claim_ingest_jobs(
    ...   SyncTimedbJobStore(""),
    ...   band="hot",
    ...   owner_token="n:h:b:1",
    ...   max_n=2,
    ... )
    []
  """
  del probe_depth
  return store.claim_ingest(
    band=band,
    owner_token=owner_token,
    ttl_s=ttl_s,
    now_s=now_s,
    max_n=max_n,
  )


def ingest_queue_census(store: SyncTimedbJobStore) -> tuple[int, int, int]:
  """
  Return (hot_queued, catchup_queued, total) for the ingest map.

  Args:
    store (SyncTimedbJobStore): In-process job store.

  Returns:
    tuple[int, int, int]: Hot depth, catchup depth, and total queued.

  Examples:
    >>> ingest_queue_census(SyncTimedbJobStore(""))
    (0, 0, 0)
  """
  if store is None:
    return (0, 0, 0)
  hot_lo, hot_hi = ingest_score_range("hot")
  catch_lo, catch_hi = ingest_score_range("catchup")
  return (
    store.ingest_count_in_score_range(hot_lo, hot_hi),
    store.ingest_count_in_score_range(catch_lo, catch_hi),
    store.queued_count(JOB_KIND_INGEST),
  )


def queue_has_capacity(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  limit: int | None = None,
) -> bool:
  """
  Return True when a queue is below its configured capacity bound.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    limit (int | None): Capacity override; default queue_capacity_limit.

  Returns:
    bool: True when another enqueue is allowed.

  Examples:
    >>> queue_has_capacity(SyncTimedbJobStore(""), kind="ingest", limit=10)
    True
  """
  cap = queue_capacity_limit() if limit is None else int(limit)
  return queue_depth(store, kind=kind) < cap


def queue_depth(store: SyncTimedbJobStore, *, kind: str) -> int:
  """
  Return the queued (not in-flight) depth for a job kind.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.

  Returns:
    int: Queued member count.

  Examples:
    >>> queue_depth(SyncTimedbJobStore(""), kind="ingest")
    0
  """
  if store is None:
    return 0
  return store.queued_count(kind)


def queue_census(store: SyncTimedbJobStore) -> dict[str, dict[str, int]]:
  """
  Return per-kind queued and in-flight counts.

  Args:
    store (SyncTimedbJobStore): In-process job store.

  Returns:
    dict[str, dict[str, int]]: kind mapped to queued and inflight.

  Examples:
    >>> queue_census(SyncTimedbJobStore(""))["ingest"]["queued"]
    0
  """
  out: dict[str, dict[str, int]] = {}
  for kind in JOB_KINDS_ALL:
    out[kind] = {
      "queued": queue_depth(store, kind=kind),
      "inflight": store.inflight_count(kind),
    }
  return out


def format_queue_census(census: dict[str, dict[str, int]]) -> str:
  """
  Render queue_census output as a compact log field.

  Args:
    census (dict[str, dict[str, int]]): Census mapping.

  Returns:
    str: kind=inflight/queued pairs joined by spaces.

  Examples:
    >>> format_queue_census({"ingest": {"queued": 2, "inflight": 1}})
    'ingest=1/2'
  """
  parts = []
  for kind in JOB_KINDS_ALL:
    entry = census.get(kind)
    if not entry:
      continue
    parts.append(
      f"{kind}={entry.get('inflight', 0)}/{entry.get('queued', 0)}",
    )
  return " ".join(parts)


def job_max_attempts() -> int:
  """
  Return the attempt ceiling before a job is dead-lettered.

  Returns:
    int: Positive attempt ceiling.

  Examples:
    >>> job_max_attempts() >= 1
    True
  """
  try:
    raw = int(cfg.get_sync_archive_retry_max_attempts())
  except Exception:
    raw = JOB_ATTEMPT_MAX_DEFAULT
  return max(1, raw)


def bump_job_attempt(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
) -> int:
  """
  Increment and return the attempt counter for a job identity.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    int: Attempt count after the increment.

  Examples:
    >>> bump_job_attempt(SyncTimedbJobStore(""), kind="ingest", identity="x")
    1
  """
  with store._lock:
    fields = store._payloads.setdefault((str(kind), str(identity)), {})
    nxt = int(fields.get("attempt") or 0) + 1
    fields["attempt"] = str(nxt)
    if store._identity_is_queued_locked(kind, identity):
      store._shadow_sync_payload_locked(kind, identity)
      store._mark_dirty_locked()
  store._maybe_persist_locked()
  return nxt


def read_job_attempt(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
) -> int:
  """
  Read the recorded attempt counter for a job identity.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    int: Attempt count, or 0 when unset.

  Examples:
    >>> read_job_attempt(SyncTimedbJobStore(""), kind="ingest", identity="x")
    0
  """
  with store._lock:
    raw = store._payloads.get((str(kind), str(identity)), {}).get(
      "attempt",
    )
  try:
    return int(raw or 0)
  except TypeError, ValueError:
    return 0


def read_inflight_entry(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
) -> tuple[float, str, float | None] | None:
  """
  Return one in-flight entry as (deadline, owner, score).

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    tuple | None: Parsed tuple, or None when the identity is not in flight.

  Examples:
    >>> read_inflight_entry(
    ...   SyncTimedbJobStore(""),
    ...   kind="ingest",
    ...   identity="a",
    ... ) is None
    True
  """
  with store._lock:
    return store._inflight.get(str(kind), {}).get(str(identity))


def read_inflight_entries(
  store: SyncTimedbJobStore,
  *,
  kind: str,
) -> dict[str, tuple[float, str, float | None]]:
  """
  Return the in-flight map for a kind as parsed tuples.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.

  Returns:
    dict[str, tuple]: Identity mapped to deadline, owner, and score.

  Examples:
    >>> read_inflight_entries(SyncTimedbJobStore(""), kind="ingest")
    {}
  """
  with store._lock:
    return dict(store._inflight.get(str(kind), {}))


def count_inflight_by_band(store: SyncTimedbJobStore) -> tuple[int, int]:
  """
  Count in-flight ingest claims per band from recorded scores.

  Args:
    store (SyncTimedbJobStore): In-process job store.

  Returns:
    tuple[int, int]: (hot_inflight, catchup_inflight).

  Examples:
    >>> count_inflight_by_band(SyncTimedbJobStore(""))
    (0, 0)
  """
  hot = 0
  catchup = 0
  for _ident, (_deadline, _owner, score) in read_inflight_entries(
    store,
    kind=JOB_KIND_INGEST,
  ).items():
    if score is None or decode_ingest_band(score) == "hot":
      hot += 1
    else:
      catchup += 1
  return (hot, catchup)


def queue_dead_letter_path(archive_data_dir: str) -> str:
  """
  Return the sidecar path for queue dead letters.

  Args:
    archive_data_dir (str): Archive data directory root.

  Returns:
    str: Absolute path to the queue dead-letter artifact.

  Examples:
    >>> queue_dead_letter_path("/a").endswith("queue_dead_letter.json")
    True
  """
  return artifact_path(archive_data_dir, QUEUE_DEAD_LETTER_KIND)


NON_FATAL_DEAD_LETTER_REASONS = frozenset(
  {
    "skip_cgroup_headroom",
    "skip_budget_bytes",
    "skip_file_cache_pressure",
    "skip_fp",
    "fp_refresh",
  }
)
_DEAD_LETTER_INDEX_CACHE: dict[
  str, tuple[int, dict[tuple[str, str], dict]]
] = {}
_DEAD_LETTER_LOCK = threading.Lock()
# Serializes load-modify-save of the dead-letter sidecar across the ingest,
# append and day_close coordinator threads (no lost updates).
_DEAD_LETTER_WRITE_LOCK = threading.RLock()


def _dead_letter_schedule(entry: dict[str, Any]) -> list[float]:
  """
  Return the cooldown schedule that applies to one dead-letter entry.

  Args:
    entry (dict[str, Any]): Dead-letter entry (``env`` selects the longer
      environment schedule).

  Returns:
    list[float]: Seconds per cycle.

  Examples:
    >>> len(_dead_letter_schedule({"env": False})) >= 1  # doctest: +SKIP
    True
  """
  if entry.get("env"):
    return list(cfg.get_sync_dead_letter_env_backoff_s())
  return list(cfg.get_sync_dead_letter_readmit_backoff_s())


def _normalize_dead_letter_entry(entry: dict[str, Any]) -> dict[str, Any]:
  """
  Fill ``cycle`` / ``readmit_at`` / ``terminal`` on legacy entries (DE-21).

  Entries written before cooldowns existed count as cycle 1 and re-admit
  ``backoff[0]`` after ``recorded_at``; reasons that current code no longer
  treats as fatal (fill skips) re-admit immediately.

  Args:
    entry (dict[str, Any]): Raw sidecar entry.

  Returns:
    dict[str, Any]: The same entry, normalized in place.

  Examples:
    >>> _normalize_dead_letter_entry(  # doctest: +SKIP
    ...   {
    ...     "kind": "ingest",
    ...     "identity": "/a",
    ...     "reason": "skip_fp",
    ...     "recorded_at": 5.0,
    ...   }
    ... )["readmit_at"]
    5.0
  """
  if "cycle" in entry:
    return entry
  recorded = float(entry.get("recorded_at") or 0.0)
  reason = str(entry.get("reason") or "")
  entry["cycle"] = 1
  entry["terminal"] = False
  entry["env"] = reason.startswith("env:")
  entry["readmit_at"] = (
    recorded
    if reason in NON_FATAL_DEAD_LETTER_REASONS
    else recorded + _dead_letter_schedule(entry)[0]
  )
  return entry


def _load_dead_letter_entries(archive_data_dir: str) -> list[dict[str, Any]]:
  """
  Load and normalize the queue dead-letter list (empty when absent).

  Args:
    archive_data_dir (str): Archive data directory root.

  Returns:
    list[dict[str, Any]]: Normalized entries.

  Examples:
    >>> _load_dead_letter_entries("/nonexistent-dir")
    []
  """
  path = queue_dead_letter_path(archive_data_dir)
  entries = load_persistence_document(path, QUEUE_DEAD_LETTER_KIND, default=[])
  if not isinstance(entries, list):
    return []
  return [
    _normalize_dead_letter_entry(e) for e in entries if isinstance(e, dict)
  ]


def _dead_letter_index(
  archive_data_dir: str,
) -> dict[tuple[str, str], dict[str, Any]]:
  """
  Return ``(kind, identity) -> entry`` cached by sidecar mtime (hot path).

  Reconstruct and handoff consult the dead letter per path; the cache keeps
  that to one ``stat`` unless the sidecar changed.

  Args:
    archive_data_dir (str): Archive data directory root.

  Returns:
    dict[tuple[str, str], dict[str, Any]]: Latest entry per kind/identity.

  Examples:
    >>> _dead_letter_index("/nonexistent-dir")
    {}
  """
  path = queue_dead_letter_path(archive_data_dir)
  try:
    mtime_ns = os.stat(path).st_mtime_ns
  except OSError:
    return {}
  with _DEAD_LETTER_LOCK:
    cached = _DEAD_LETTER_INDEX_CACHE.get(path)
    if cached is not None and cached[0] == mtime_ns:
      return cached[1]
  index = {
    (str(e.get("kind") or ""), str(e.get("identity") or "")): e
    for e in _load_dead_letter_entries(archive_data_dir)
  }
  with _DEAD_LETTER_LOCK:
    _DEAD_LETTER_INDEX_CACHE[path] = (mtime_ns, index)
  return index


def _save_dead_letter_entries(
  archive_data_dir: str,
  entries: list[dict[str, Any]],
) -> bool:
  """
  Persist the dead-letter list and drop the cached index.

  Args:
    archive_data_dir (str): Archive data directory root.
    entries (list[dict[str, Any]]): Entries to write.

  Returns:
    bool: True when written.

  Examples:
    >>> _save_dead_letter_entries("/nonexistent-dir", [])
    False
  """
  path = queue_dead_letter_path(archive_data_dir)
  try:
    save_persistence_document(path, QUEUE_DEAD_LETTER_KIND, entries)
  except OSError:
    return False
  with _DEAD_LETTER_LOCK:
    _DEAD_LETTER_INDEX_CACHE.pop(path, None)
  return True


def upsert_queue_dead_letter(
  archive_data_dir: str,
  *,
  kind: str,
  identity: str,
  attempt: int,
  reason: str,
  env: bool = False,
  now: float | None = None,
  max_entries: int = 5000,
) -> dict[str, Any] | None:
  """
  Record (or advance) a job that exhausted its retries: cooldown, then terminal.

  One entry per kind+identity (DE-1): each give-up bumps ``cycle`` and sets
  ``readmit_at = now + backoff[cycle-1]``; past the last cooldown the entry is
  ``terminal`` and the caller resolves it (quarantine the raw file, or an ERROR
  for a day). The FIFO trim drops terminal entries first so a live cooldown is
  never evicted silently.

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.
    attempt (int): Attempt count at give-up time.
    reason (str): Short failure reason (``env:*`` for environment errors).
    env (bool): Use the environment cooldown schedule.
    now (float | None): Clock override (tests).
    max_entries (int): Cap on retained entries.

  Returns:
    dict[str, Any] | None: The stored entry, or None when the write failed.

  Examples:
    >>> upsert_queue_dead_letter(
    ...   "/nonexistent-dir",
    ...   kind="ingest",
    ...   identity="x",
    ...   attempt=5,
    ...   reason="boom",
    ... ) is None
    True
  """
  ts = time.time() if now is None else float(now)
  with _DEAD_LETTER_WRITE_LOCK:
    return _upsert_queue_dead_letter_locked(
      archive_data_dir,
      str(kind),
      str(identity),
      int(attempt),
      str(reason),
      bool(env),
      ts,
      int(max_entries),
    )


def _upsert_queue_dead_letter_locked(
  archive_data_dir: str,
  kind: str,
  identity: str,
  attempt: int,
  reason: str,
  env: bool,
  ts: float,
  max_entries: int,
) -> dict[str, Any] | None:
  """
  Body of ``upsert_queue_dead_letter`` (caller holds the write lock).

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.
    attempt (int): Attempt count at give-up time.
    reason (str): Failure reason.
    env (bool): Use the environment cooldown schedule.
    ts (float): Current time.
    max_entries (int): Cap on retained entries.

  Returns:
    dict[str, Any] | None: Stored entry, or None when the write failed.

  Examples:
    >>> _upsert_queue_dead_letter_locked(  # doctest: +SKIP
    ...   "/a", "ingest", "/x", 5, "r", False, 0.0, 10
    ... )
  """
  entries = _load_dead_letter_entries(archive_data_dir)
  key = (kind, identity)
  prior = next(
    (e for e in entries if (e.get("kind"), e.get("identity")) == key), None
  )
  entries = [e for e in entries if e is not prior]
  entry = {
    "kind": key[0],
    "identity": key[1],
    "attempt": int(attempt),
    "reason": str(reason)[:500],
    "recorded_at": ts,
    "first_recorded_at": (prior or {}).get("first_recorded_at")
    or (prior or {}).get("recorded_at")
    or ts,
    "cycle": int((prior or {}).get("cycle") or 0) + 1,
    "env": bool(env or str(reason).startswith("env:")),
  }
  schedule = _dead_letter_schedule(entry)
  entry["terminal"] = entry["cycle"] > len(schedule)
  entry["readmit_at"] = (
    None if entry["terminal"] else ts + schedule[entry["cycle"] - 1]
  )
  entries.append(entry)
  if len(entries) > max_entries:
    overflow = len(entries) - max_entries
    terminal_old = [e for e in entries if e.get("terminal")][:overflow]
    drop = {id(e) for e in terminal_old}
    entries = [e for e in entries if id(e) not in drop]
    if len(entries) > max_entries:
      entries = entries[-max_entries:]
  if not _save_dead_letter_entries(archive_data_dir, entries):
    return None
  return entry


def append_queue_dead_letter(
  archive_data_dir: str,
  *,
  kind: str,
  identity: str,
  attempt: int,
  reason: str,
  max_entries: int = 5000,
) -> bool:
  """
  Record a job that exhausted its retries (see ``upsert_queue_dead_letter``).

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.
    attempt (int): Attempt count at give-up time.
    reason (str): Short failure reason.
    max_entries (int): Cap on retained entries.

  Returns:
    bool: True when the entry was persisted.

  Examples:
    >>> append_queue_dead_letter(
    ...   "/nonexistent-dir",
    ...   kind="ingest",
    ...   identity="x",
    ...   attempt=9,
    ...   reason="boom",
    ... )
    False
  """
  return (
    upsert_queue_dead_letter(
      archive_data_dir,
      kind=kind,
      identity=identity,
      attempt=attempt,
      reason=reason,
      max_entries=max_entries,
    )
    is not None
  )


def dead_letter_blocked(
  archive_data_dir: str,
  *,
  kind: str,
  identity: str,
  now: float | None = None,
) -> bool:
  """
  Return True while kind/identity is in dead-letter cooldown or terminal.

  Replaces the permanent "ever dead-lettered" check (DE-1): once
  ``readmit_at`` passes, reconstruct and handoff may enqueue the path again.

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.
    now (float | None): Clock override (tests).

  Returns:
    bool: True when enqueue must be skipped.

  Examples:
    >>> dead_letter_blocked("/nope", kind="ingest", identity="x")
    False
  """
  if not archive_data_dir or not identity:
    return False
  entry = _dead_letter_index(archive_data_dir).get((str(kind), str(identity)))
  if entry is None:
    return False
  if entry.get("terminal"):
    return True
  ts = time.time() if now is None else float(now)
  return ts < float(entry.get("readmit_at") or 0.0)


def remove_queue_dead_letter(
  archive_data_dir: str,
  *,
  kind: str,
  identity: str,
) -> bool:
  """
  Drop the dead-letter entry for kind/identity after a successful job.

  Cheap when absent (cached index lookup, no write).

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    bool: True when an entry was removed.

  Examples:
    >>> remove_queue_dead_letter("/nope", kind="ingest", identity="x")
    False
  """
  if not archive_data_dir or not identity:
    return False
  key = (str(kind), str(identity))
  if key not in _dead_letter_index(archive_data_dir):
    return False
  with _DEAD_LETTER_WRITE_LOCK:
    entries = [
      e
      for e in _load_dead_letter_entries(archive_data_dir)
      if (e.get("kind"), e.get("identity")) != key
    ]
    return _save_dead_letter_entries(archive_data_dir, entries)


def due_dead_letter_readmissions(
  archive_data_dir: str,
  *,
  now: float | None = None,
  limit: int = 500,
) -> list[dict[str, Any]]:
  """
  Return non-terminal entries whose cooldown elapsed and were not re-admitted.

  Args:
    archive_data_dir (str): Archive data directory root.
    now (float | None): Clock override (tests).
    limit (int): Batch cap (A17 rate limit).

  Returns:
    list[dict[str, Any]]: Oldest-due first, at most ``limit``.

  Examples:
    >>> due_dead_letter_readmissions("/nope")
    []
  """
  if not archive_data_dir:
    return []
  ts = time.time() if now is None else float(now)
  due = [
    e
    for e in _dead_letter_index(archive_data_dir).values()
    if not e.get("terminal")
    and float(e.get("readmit_at") or 0.0) <= ts
    and float(e.get("readmitted_at") or -1.0)
    < float(e.get("readmit_at") or 0.0)
  ]
  due.sort(key=lambda e: float(e.get("readmit_at") or 0.0))
  return due[: max(0, int(limit))]


def mark_dead_letters_readmitted(
  archive_data_dir: str,
  keys: list[tuple[str, str]],
  *,
  now: float | None = None,
) -> bool:
  """
  Stamp ``readmitted_at`` so a due entry is re-admitted once per cycle.

  Args:
    archive_data_dir (str): Archive data directory root.
    keys (list[tuple[str, str]]): ``(kind, identity)`` pairs re-admitted.
    now (float | None): Clock override (tests).

  Returns:
    bool: True when written (or nothing to do).

  Examples:
    >>> mark_dead_letters_readmitted("/nope", [])
    True
  """
  if not keys:
    return True
  ts = time.time() if now is None else float(now)
  want = {(str(k), str(i)) for k, i in keys}
  with _DEAD_LETTER_WRITE_LOCK:
    entries = _load_dead_letter_entries(archive_data_dir)
    for e in entries:
      if (e.get("kind"), e.get("identity")) in want:
        e["readmitted_at"] = ts
    return _save_dead_letter_entries(archive_data_dir, entries)


def parse_lease_owner(owner_token: str) -> LeaseOwner:
  """
  Parse an owner token into its identity components.

  Args:
    owner_token (str): Value stored with a claim.

  Returns:
    LeaseOwner: Parsed components.

  Examples:
    >>> parse_lease_owner("n:h:b:7").hostname
    'h'
  """
  text = str(owner_token or "")
  parts = text.split(":")
  pid: int | None = None
  if len(parts) >= 2:
    try:
      pid = int(parts[-1])
    except ValueError:
      pid = None
  if len(parts) >= 4:
    return LeaseOwner(parts[0], parts[-3], parts[-2], pid)
  nonce = parts[0] if parts else ""
  return LeaseOwner(nonce, "", "", pid)


def parse_lease_owner_pid(owner_token: str) -> int | None:
  """
  Return the PID embedded in an owner token.

  Args:
    owner_token (str): Owner token.

  Returns:
    int | None: PID, or None when unparsable.

  Examples:
    >>> parse_lease_owner_pid("n:h:b:7")
    7
  """
  return parse_lease_owner(owner_token).pid


def lease_owner_is_locally_evaluable(
  owner: LeaseOwner,
  *,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> bool:
  """
  Return True when this process can judge the owner's PID liveness.

  Args:
    owner (LeaseOwner): Parsed owner token.
    hostname (str | None): Local hostname override for tests.
    boot_id (str | None): Local boot id override for tests.

  Returns:
    bool: True when host and boot match this process.

  Examples:
    >>> lease_owner_is_locally_evaluable(
    ...   LeaseOwner("n", "h", "b", 1),
    ...   hostname="h",
    ...   boot_id="b",
    ... )
    True
  """
  if not owner.hostname or not owner.boot_id:
    return False
  if hostname is None:
    try:
      host = socket.gethostname()
    except OSError:
      host = "unknown"
  else:
    host = hostname
  boot = current_boot_id() if boot_id is None else boot_id
  return owner.hostname == str(host).replace(":", "_").replace(
    "|", "_"
  ) and owner.boot_id == str(boot).replace(":", "_").replace("|", "_")


def _pid_is_alive(pid: int) -> bool:
  """
  Return True when a local PID is still alive.

  Args:
    pid (int): Process id to probe.

  Returns:
    bool: True when os.kill(pid, 0) succeeds.

  Examples:
    >>> _pid_is_alive(os.getpid())
    True
  """
  try:
    os.kill(int(pid), 0)
  except OSError:
    return False
  return True


def renew_job_lease(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  owner_token: str,
  ttl_s: int | None = None,
  now_s: float | None = None,
) -> bool:
  """
  Extend the in-memory deadline when this owner still holds the claim.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Owner token from the claim.
    ttl_s (int | None): Deadline override.
    now_s (float | None): Clock override for tests.

  Returns:
    bool: True when the claim was still owned and got extended.

  Examples:
    >>> renew_job_lease(
    ...   SyncTimedbJobStore(""),
    ...   kind="ingest",
    ...   identity="x",
    ...   owner_token="n:h:b:1",
    ... )
    False
  """
  if not owner_token:
    return False
  ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
  now = time.time() if now_s is None else float(now_s)
  ident = str(identity)
  with store._lock:
    entry = store._inflight.get(kind, {}).get(ident)
    if entry is None or entry[1] != owner_token:
      return False
    _deadline, owner, score = entry
    store._inflight[kind][ident] = (now + ttl, owner, score)
    store._leases[(kind, ident)] = owner_token
    return True


def reap_expired_inflight(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  now_s: float | None = None,
  limit: int = 64,
  ttl_s: int | None = None,
) -> list[str]:
  """
  Requeue in-flight jobs whose deadline plus grace has passed.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    now_s (float | None): Clock override for tests.
    limit (int): Maximum identities to recover in one call.
    ttl_s (int | None): Unused leftover from the grace formula.

  Returns:
    list[str]: Identities returned to the queue.

  Examples:
    >>> reap_expired_inflight(SyncTimedbJobStore(""), kind="ingest")
    []
  """
  del ttl_s
  now = time.time() if now_s is None else float(now_s)
  cutoff = now - float(INFLIGHT_REAP_GRACE_FLOOR_S)
  want = max(1, int(limit))
  out: list[str] = []
  with store._lock:
    items = list(store._inflight.get(kind, {}).items())
    for ident, (deadline, _owner, score) in items:
      if len(out) >= want:
        break
      if float(deadline) > cutoff:
        continue
      store._inflight[kind].pop(ident, None)
      store._leases.pop((kind, ident), None)
      if str(kind) == JOB_KIND_INGEST:
        store._ingest_set_locked(ident, penalize_ingest_score(score))
      else:
        store._lists[kind].append(ident)
        store._pending[kind].add(ident)
        store._shadow_lists[kind].append(ident)
        store._shadow_pending[kind].add(ident)
        store._shadow_sync_payload_locked(kind, ident)
      out.append(ident)
      store._mark_dirty_locked()
  if out:
    store.persist(force=False)
  return out


def steal_dead_owner_leases(
  store: SyncTimedbJobStore,
  *,
  pid_alive_fn: Any | None = None,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> int:
  """
  Requeue in-memory claims whose local owner PID is dead.

  Same-process threads share this PID, so a healthy supervisor typically
  steals nothing. After a crash, inflight is empty and reconstruct refills.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    pid_alive_fn (Any | None): Optional callable(pid) -> bool.
    hostname (str | None): Local hostname override for tests.
    boot_id (str | None): Local boot id override for tests.

  Returns:
    int: Number of leases stolen.

  Examples:
    >>> steal_dead_owner_leases(SyncTimedbJobStore(""))
    0
  """
  if store is None:
    return 0
  alive_fn = _pid_is_alive if pid_alive_fn is None else pid_alive_fn
  stolen = 0
  with store._lock:
    items = list(store._leases.items())
  for (kind, ident), owner_token in items:
    owner = parse_lease_owner(owner_token)
    if owner.pid is None:
      continue
    if not lease_owner_is_locally_evaluable(
      owner,
      hostname=hostname,
      boot_id=boot_id,
    ):
      continue
    if alive_fn(owner.pid):
      continue
    if store.requeue(
      kind=kind,
      identity=ident,
      owner_token=owner_token,
    ):
      stolen += 1
  return stolen


def reconcile_this_owner_orphan_leases(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  local_identities: Iterable[str],
  owner_token: str,
) -> int:
  """
  Requeue this-owner inflight identities missing from local worker maps.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    local_identities (Iterable[str]): Identities the coordinator still
      tracks.
    owner_token (str): Owner token minted by this process.

  Returns:
    int: Number of orphan claims requeued.

  Examples:
    >>> reconcile_this_owner_orphan_leases(
    ...   SyncTimedbJobStore(""),
    ...   kind="ingest",
    ...   local_identities=(),
    ...   owner_token="n:h:b:1",
    ... )
    0
  """
  known = {str(ident) for ident in local_identities}
  recovered = 0
  for ident, (_deadline, owner, score) in list(
    read_inflight_entries(store, kind=kind).items(),
  ):
    if ident in known or owner != owner_token:
      continue
    if store.requeue(
      kind=kind,
      identity=ident,
      owner_token=owner,
      score=score,
    ):
      recovered += 1
  return recovered


def reset_job_queue_script_cache_for_tests() -> None:
  """
  Clear memoized boot-id state used by unit tests.

  Returns:
    None

  Examples:
    >>> reset_job_queue_script_cache_for_tests()
  """
  global _BOOT_ID_CACHE
  _BOOT_ID_CACHE = None


def steal_job_lease_if_owner_dead(
  store: SyncTimedbJobStore,
  *,
  kind: str,
  identity: str,
  pid_alive_fn: Any | None = None,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> bool:
  """
  Steal one lease when its local owner PID is dead.

  Args:
    store (SyncTimedbJobStore): In-process job store.
    kind (str): Job kind.
    identity (str): Job identity.
    pid_alive_fn (Any | None): Optional callable(pid) -> bool.
    hostname (str | None): Local hostname override for tests.
    boot_id (str | None): Local boot id override for tests.

  Returns:
    bool: True when the lease was stolen and requeued, or when a
    dead-owner lease with no inflight entry was dropped (no fabricated
    LIST job).

  Examples:
    >>> steal_job_lease_if_owner_dead(
    ...   SyncTimedbJobStore(""),
    ...   kind="ingest",
    ...   identity="x",
    ... )
    False
  """
  if store is None:
    return False
  alive_fn = _pid_is_alive if pid_alive_fn is None else pid_alive_fn
  with store._lock:
    owner_token = store._leases.get((str(kind), str(identity)))
  if not owner_token:
    return False
  owner = parse_lease_owner(owner_token)
  if owner.pid is None:
    return False
  if not lease_owner_is_locally_evaluable(
    owner,
    hostname=hostname,
    boot_id=boot_id,
  ):
    return False
  if alive_fn(owner.pid):
    return False
  if store.requeue(
    kind=kind,
    identity=identity,
    owner_token=owner_token,
  ):
    return True
  # Orphan lease with no inflight map: drop the token, do not fabricate
  # a LIST/ZSET member (same class as job-store steal with nil HASH).
  with store._lock:
    current = store._leases.get((str(kind), str(identity)))
    if current != owner_token:
      return False
    store._leases.pop((str(kind), str(identity)), None)
  return True
