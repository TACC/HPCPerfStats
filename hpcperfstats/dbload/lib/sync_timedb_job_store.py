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
  LEASE_CONFLICT_SCORE_PENALTY: Score bump applied on retry requeue.
  LeaseOwner: Parsed owner token.
  QUEUE_DEAD_LETTER_KIND: Persistence kind for queue dead letters.
  QUEUE_MAX_MEMBERS_FLOOR: Minimum accepted queue capacity bound.
  SCORE_STRIDE: Day/tie-break stride inside a band score range.
  SyncTimedbJobStore: Thread-safe in-process store.
  _BOOT_ID_CACHE: Memoized host boot identifier.
  _StorePipeline: Sequential census command collector.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

import hashlib
import os
import secrets
import socket
import threading
import time

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    artifact_path,
    load_persistence_document,
    save_persistence_document,
)

JOB_KIND_DISCOVER = "discover"
JOB_KIND_INGEST = "ingest"
JOB_KIND_APPEND = "append"
JOB_KIND_DAY_CLOSE = "day_close"
JOB_KINDS_LIST = (JOB_KIND_DISCOVER, JOB_KIND_APPEND, JOB_KIND_DAY_CLOSE)
JOB_KINDS_ALL = (JOB_KIND_INGEST,) + JOB_KINDS_LIST

HOT_SCORE_BASE = 0
CATCHUP_SCORE_BASE = 10**15
SCORE_STRIDE = 10**6
LEASE_CONFLICT_SCORE_PENALTY = SCORE_STRIDE
JOB_ATTEMPT_MAX_DEFAULT = 5
QUEUE_MAX_MEMBERS_FLOOR = 1000
QUEUE_DEAD_LETTER_KIND = "queue_dead_letter"
JOB_STORE_SNAPSHOT_KIND = "job_store_snapshot"
JOB_STORE_SNAPSHOT_RELPATH = ".sync_timedb_job_store.json"
ARCHIVE_MEMBERS_STORE_DIR_RELPATH = ".sync_timedb_archive_members"
JOB_STORE_PERSIST_INTERVAL_S = 5.0
JOB_LEASE_TTL_FLOOR_S = 60
INFLIGHT_REAP_GRACE_FLOOR_S = 30

_BOOT_ID_CACHE: Optional[str] = None


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
    """

    kind: str
    identity: str
    owner_token: str
    deadline: float
    score: Optional[float]
    fingerprint: str = ""


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
    pid: Optional[int]


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
      _inflight: Per-kind in-flight map of deadline, owner, and score.
      _last_persist: Epoch seconds of the last successful snapshot write.
      _leases: Owner token by (kind, identity).
      _lists: FIFO deques for discover, append, and day_close.
      _lock: Re-entrant lock covering every mutation.
      _payloads: Fingerprint and attempt fields by (kind, identity).
      _pending: Dedupe sets mirroring queued LIST identities.
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
        self._lock = threading.RLock()
        self._ingest: Dict[str, float] = {}
        self._lists: Dict[str, Deque[str]] = {
            kind: deque() for kind in JOB_KINDS_LIST
        }
        self._pending: Dict[str, set[str]] = {
            kind: set() for kind in JOB_KINDS_LIST
        }
        self._inflight: Dict[str, Dict[str, Tuple[float, str, Optional[float]]]] = {
            kind: {} for kind in JOB_KINDS_ALL
        }
        self._leases: Dict[Tuple[str, str], str] = {}
        self._payloads: Dict[Tuple[str, str], Dict[str, str]] = {}
        self._dirty = False
        self._last_persist = 0.0
        if self.archive_dir:
            os.makedirs(self.archive_dir, exist_ok=True)
            self.load()

    def ingest_identities(self) -> List[str]:
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
            return [
                ident
                for ident, _score in sorted(
                    self._ingest.items(), key=lambda item: item[1],
                )
            ]

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
        Write queued work to the registered snapshot sidecar.

        In-flight claims and owner tokens are omitted so a crash reconstructs
        from disk and the database instead of resurrecting stale leases.
        Payload rows for identities that are neither queued nor in-flight are
        dropped from memory; the snapshot writes only queued payloads.

        Args:
          force (bool): Ignore the persist interval and write immediately.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbJobStore("/tmp/empty")
          >>> store.persist(force=True)
        """
        now = time.time()
        with self._lock:
            if not self.archive_dir:
                return
            if (
                not force
                and not self._dirty
            ):
                return
            if (
                not force
                and (now - self._last_persist) < self.persist_interval_s
            ):
                return
            queued = self._queued_identities_locked()
            live = queued | self._inflight_identities_locked()
            self._prune_orphan_payloads_locked(active=live)
            payload = {
                "ingest": dict(self._ingest),
                "lists": {
                    kind: list(items) for kind, items in self._lists.items()
                },
                "pending": {
                    kind: sorted(idents)
                    for kind, idents in self._pending.items()
                },
                "payloads": {
                    "%s|%s" % (kind, ident): dict(fields)
                    for (kind, ident), fields in self._payloads.items()
                    if (kind, ident) in queued
                },
            }
            path = artifact_path(self.archive_dir, JOB_STORE_SNAPSHOT_KIND)
            save_persistence_document(path, JOB_STORE_SNAPSHOT_KIND, payload)
            self._dirty = False
            self._last_persist = now

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
            path, JOB_STORE_SNAPSHOT_KIND, default={},
        )
        if not isinstance(raw, dict):
            return
        ingest = raw.get("ingest") or {}
        lists = raw.get("lists") or {}
        pending = raw.get("pending") or {}
        payloads = raw.get("payloads") or {}
        with self._lock:
            self._ingest = {
                str(ident): float(score)
                for ident, score in ingest.items()
            }
            for kind in JOB_KINDS_LIST:
                self._lists[kind] = deque(
                    str(item) for item in (lists.get(kind) or [])
                )
                self._pending[kind] = {
                    str(item) for item in (pending.get(kind) or [])
                }
            self._payloads = {}
            if isinstance(payloads, dict):
                for key, fields in payloads.items():
                    if not isinstance(fields, dict) or "|" not in str(key):
                        continue
                    kind, ident = str(key).split("|", 1)
                    self._payloads[(kind, ident)] = {
                        str(name): str(value) for name, value in fields.items()
                    }
            for kind in JOB_KINDS_ALL:
                self._inflight[kind] = {}
            self._leases.clear()
            self._prune_orphan_payloads_locked(
                active=self._queued_identities_locked(),
            )
            self._dirty = False

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
        self._dirty = True

    def _maybe_persist_locked(self) -> None:
        """
        Persist outside the caller lock after a durable mutation.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbJobStore("/tmp/empty")
          >>> store._maybe_persist_locked()
        """
        dirty = self._dirty
        if not dirty:
            return
        # persist() takes the lock again via RLock.
        self.persist(force=False)

    def zadd_ingest(
        self,
        identity: str,
        score: float,
        fingerprint: str | None = None,
    ) -> int:
        """
        Queue or reband one ingest identity.

        Args:
          identity (str): Normalized ingest path.
          score (float): Band-encoded score.
          fingerprint (str | None): Optional size/mtime payload.

        Returns:
          int: 1 when the identity is newly queued, else 0.

        Examples:
          >>> store = SyncTimedbJobStore("/tmp/empty")
          >>> store.zadd_ingest("/raw/a", 1.0)
          1
        """
        ident = str(identity)
        with self._lock:
            if ident in self._inflight[JOB_KIND_INGEST]:
                return 0
            existed = ident in self._ingest
            if not existed and not self._has_capacity_locked(JOB_KIND_INGEST):
                return 0
            self._ingest[ident] = float(score)
            if fingerprint:
                self._payloads.setdefault(
                    (JOB_KIND_INGEST, ident), {},
                )["fingerprint"] = str(fingerprint)
            self._mark_dirty_locked()
        self._maybe_persist_locked()
        return 0 if existed else 1

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
            raise ValueError("kind %r is not a LIST queue kind" % (kind,))
        ident = str(identity)
        with self._lock:
            if dedupe:
                if ident in self._pending[kind] or ident in self._inflight[kind]:
                    return 0
            if not self._has_capacity_locked(kind):
                return 0
            self._lists[kind].append(ident)
            self._pending[kind].add(ident)
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
    ) -> List[ClaimedJob]:
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
        claimed: List[ClaimedJob] = []
        with self._lock:
            candidates = [
                (ident, score)
                for ident, score in sorted(
                    self._ingest.items(), key=lambda item: item[1],
                )
                if lo <= float(score) <= hi
            ]
            for ident, score in candidates:
                if len(claimed) >= want:
                    break
                if ident in self._inflight[JOB_KIND_INGEST]:
                    continue
                self._ingest.pop(ident, None)
                self._inflight[JOB_KIND_INGEST][ident] = (
                    deadline, owner_token, float(score),
                )
                self._leases[(JOB_KIND_INGEST, ident)] = owner_token
                fields = self._payloads.get((JOB_KIND_INGEST, ident), {})
                claimed.append(
                    ClaimedJob(
                        kind=JOB_KIND_INGEST,
                        identity=ident,
                        owner_token=owner_token,
                        deadline=deadline,
                        score=float(score),
                        fingerprint=str(fields.get("fingerprint") or ""),
                    ),
                )
            if claimed:
                self._mark_dirty_locked()
        if claimed:
            self._maybe_persist_locked()
        return claimed

    def claim_list(
        self,
        *,
        kind: str,
        owner_token: str,
        ttl_s: int | None = None,
        now_s: float | None = None,
    ) -> Optional[ClaimedJob]:
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
            raise ValueError("kind %r is not a LIST queue kind" % (kind,))
        if not owner_token:
            raise ValueError("owner_token is required to claim a job")
        ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
        now = time.time() if now_s is None else float(now_s)
        deadline = now + ttl
        with self._lock:
            while self._lists[kind]:
                ident = self._lists[kind].popleft()
                self._pending[kind].discard(ident)
                if ident in self._inflight[kind]:
                    continue
                self._inflight[kind][ident] = (deadline, owner_token, None)
                self._leases[(kind, ident)] = owner_token
                self._mark_dirty_locked()
                job = ClaimedJob(
                    kind=str(kind),
                    identity=ident,
                    owner_token=owner_token,
                    deadline=deadline,
                    score=None,
                )
                break
            else:
                job = None
        if job is not None:
            self._maybe_persist_locked()
        return job

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
            self._mark_dirty_locked()
        self._maybe_persist_locked()
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
                self._ingest[ident] = restore
            else:
                self._lists[kind].append(ident)
                self._pending[kind].add(ident)
            self._mark_dirty_locked()
        self._maybe_persist_locked()
        return True

    def _has_capacity_locked(self, kind: str) -> bool:
        """
        Return True when another durable member may be queued.

        Args:
          kind (str): Job kind.

        Returns:
          bool: True when the queue is below the configured cap.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty")._has_capacity_locked("ingest")
          True
        """
        cap = queue_capacity_limit()
        if str(kind) == JOB_KIND_INGEST:
            return len(self._ingest) < cap
        return len(self._lists[kind]) < cap

    def zcard(self, key: str) -> int:
        """
        Return ingest queue depth for the ingest ZSET key.

        Args:
          key (str): Queue key from job_queue_key.

        Returns:
          int: Queued ingest count, or 0 for unknown keys.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").zcard("unused")
          0
        """
        with self._lock:
            if key == job_queue_key(JOB_KIND_INGEST):
                return len(self._ingest)
            return 0

    def zcount(self, key: str, lo: Any, hi: Any) -> int:
        """
        Count ingest members whose scores fall in an inclusive range.

        Args:
          key (str): Ingest queue key.
          lo (Any): Inclusive lower bound, including -inf.
          hi (Any): Inclusive upper bound, including +inf.

        Returns:
          int: Matching member count.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").zcount("unused", 0, 1)
          0
        """
        low = _bound_to_float(lo, default=float("-inf"))
        high = _bound_to_float(hi, default=float("+inf"))
        with self._lock:
            if key != job_queue_key(JOB_KIND_INGEST):
                return 0
            return sum(
                1 for score in self._ingest.values() if low <= score <= high
            )

    def zscore(self, key: str, member: str) -> Optional[float]:
        """
        Return the queued ingest score for one identity.

        Args:
          key (str): Ingest queue key.
          member (str): Ingest identity.

        Returns:
          float | None: Score, or None when the identity is not queued.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").zscore("unused", "/a") is None
          True
        """
        with self._lock:
            if key != job_queue_key(JOB_KIND_INGEST):
                return None
            return self._ingest.get(str(member))

    def llen(self, key: str) -> int:
        """
        Return LIST queue depth for a discover/append/day_close key.

        Args:
          key (str): LIST queue key.

        Returns:
          int: Queued count.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").llen("unused")
          0
        """
        with self._lock:
            for kind in JOB_KINDS_LIST:
                if key == job_queue_key(kind):
                    return len(self._lists[kind])
            return 0

    def lrange(self, key: str, start: int, end: int) -> List[str]:
        """
        Return a slice of a LIST queue, matching inclusive LIST indexes.

        Args:
          key (str): LIST queue key.
          start (int): Inclusive start index.
          end (int): Inclusive end index; -1 means through the tail.

        Returns:
          list[str]: Identities in the requested slice.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").lrange("unused", 0, -1)
          []
        """
        with self._lock:
            items: List[str] = []
            for kind in JOB_KINDS_LIST:
                if key == job_queue_key(kind):
                    items = list(self._lists[kind])
                    break
        if not items:
            return []
        if end == -1:
            end = len(items) - 1
        if start < 0:
            start = max(0, len(items) + start)
        if end < 0:
            end = max(-1, len(items) + end)
        return items[start:end + 1]

    def hlen(self, key: str) -> int:
        """
        Return in-flight count for a kind's in-flight map key.

        Args:
          key (str): In-flight key from job_inflight_key.

        Returns:
          int: In-flight count.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").hlen("unused")
          0
        """
        with self._lock:
            for kind in JOB_KINDS_ALL:
                if key == job_inflight_key(kind):
                    return len(self._inflight[kind])
            return 0

    def hget(self, key: str, field: str) -> Optional[str]:
        """
        Read one in-flight or payload field.

        Args:
          key (str): In-flight or payload key.
          field (str): Identity or payload field name.

        Returns:
          str | None: Stored value, or None when absent.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").hget("unused", "a") is None
          True
        """
        with self._lock:
            for kind in JOB_KINDS_ALL:
                if key == job_inflight_key(kind):
                    entry = self._inflight[kind].get(str(field))
                    if entry is None:
                        return None
                    deadline, owner, score = entry
                    score_text = "" if score is None else str(score)
                    return "%.3f|%s|%s" % (deadline, owner, score_text)
                payload_key = job_payload_key(kind, str(field))
                if key == payload_key:
                    return self._payloads.get((kind, str(field)), {}).get(
                        "fingerprint",
                    )
            for (kind, ident), fields in self._payloads.items():
                if key == job_payload_key(kind, ident):
                    return fields.get(str(field))
            return None

    def get(self, key: str) -> Optional[str]:
        """
        Return the owner token for a lease key.

        Args:
          key (str): Lease key from job_lease_key.

        Returns:
          str | None: Owner token, or None when the lease is absent.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").get("unused") is None
          True
        """
        parsed = parse_job_lease_key(key)
        if parsed is None:
            return None
        kind, ident = parsed
        with self._lock:
            return self._leases.get((kind, ident))

    def hexists(self, key: str, field: str) -> bool:
        """
        Return True when an in-flight identity exists.

        Args:
          key (str): In-flight key.
          field (str): Job identity.

        Returns:
          bool: True when the identity is in flight.

        Examples:
          >>> SyncTimedbJobStore("/tmp/empty").hexists("unused", "a")
          False
        """
        with self._lock:
            for kind in JOB_KINDS_ALL:
                if key == job_inflight_key(kind):
                    return str(field) in self._inflight[kind]
            return False

    def pipeline(self, transaction: bool = False) -> "_StorePipeline":
        """
        Return a tiny pipeline that batches zcount and zcard reads.

        Args:
          transaction (bool): Unused compatibility flag.

        Returns:
          _StorePipeline: Collector that executes against this store.

        Examples:
          >>> isinstance(SyncTimedbJobStore("/tmp/empty").pipeline(), object)
          True
        """
        del transaction
        return _StorePipeline(self)


class _StorePipeline:
    """
    Sequential command collector used by ingest_zset_census.

    Attributes:
      store: Job store that executes queued commands.
      _ops: Pending callable list.
    """

    def __init__(self, store: SyncTimedbJobStore) -> None:
        """
        Bind the pipeline to one job store.

        Args:
          store (SyncTimedbJobStore): Store that will run queued commands.

        Returns:
          None

        Examples:
          >>> _StorePipeline(SyncTimedbJobStore("/tmp/empty")).execute()
          []
        """
        self.store = store
        self._ops: List[Any] = []

    def zcount(self, key: str, lo: Any, hi: Any) -> "_StorePipeline":
        """
        Queue a zcount read.

        Args:
          key (str): Ingest queue key.
          lo (Any): Inclusive lower bound.
          hi (Any): Inclusive upper bound.

        Returns:
          _StorePipeline: This pipeline.

        Examples:
          >>> p = _StorePipeline(SyncTimedbJobStore("/tmp/empty"))
          >>> p.zcount("k", 0, 1) is p
          True
        """
        self._ops.append(lambda: self.store.zcount(key, lo, hi))
        return self

    def zcard(self, key: str) -> "_StorePipeline":
        """
        Queue a zcard read.

        Args:
          key (str): Ingest queue key.

        Returns:
          _StorePipeline: This pipeline.

        Examples:
          >>> p = _StorePipeline(SyncTimedbJobStore("/tmp/empty"))
          >>> p.zcard("k") is p
          True
        """
        self._ops.append(lambda: self.store.zcard(key))
        return self

    def execute(self) -> List[Any]:
        """
        Run queued commands in order and return their results.

        Returns:
          list: Per-command return values.

        Examples:
          >>> _StorePipeline(SyncTimedbJobStore("/tmp/empty")).execute()
          []
        """
        return [op() for op in self._ops]


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


def job_queue_key(kind: str) -> str:
    """
    Return the stable queue key name for a job kind.

    Args:
      kind (str): discover, ingest, append, or day_close.

    Returns:
      str: Queue key used by census helpers.

    Raises:
      ValueError: When kind is unknown.

    Examples:
      >>> job_queue_key("ingest").endswith(":queue:ingest")
      True
    """
    text = str(kind or "").strip()
    if text == JOB_KIND_INGEST:
        return "hps:job:queue:ingest"
    if text in JOB_KINDS_LIST:
        return "hps:job:queue:%s" % text
    raise ValueError("unknown job kind %r" % (kind,))


def job_inflight_key(kind: str) -> str:
    """
    Return the in-flight map key for a job kind.

    Args:
      kind (str): Job kind.

    Returns:
      str: In-flight key.

    Raises:
      ValueError: When kind is unknown.

    Examples:
      >>> job_inflight_key("ingest").endswith(":inflight:ingest")
      True
    """
    text = str(kind or "").strip()
    if text not in JOB_KINDS_ALL:
        raise ValueError("unknown job kind %r" % (kind,))
    return "hps:job:inflight:%s" % text


def job_lease_key(kind: str, identity: str) -> str:
    """
    Return the lease key for one identity.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      str: Lease key.

    Raises:
      ValueError: When kind or identity is empty.

    Examples:
      >>> ":lease:ingest:" in job_lease_key("ingest", "p|1|2")
      True
    """
    kind_text = str(kind or "").strip()
    ident = str(identity or "").strip()
    if not kind_text or not ident:
        raise ValueError("kind and identity are required for a lease key")
    return "hps:job:lease:%s:%s" % (kind_text, ident)


def job_payload_key(kind: str, identity: str) -> str:
    """
    Return the payload key for one identity.

    Args:
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      str: Payload key.

    Raises:
      ValueError: When kind or identity is empty.

    Examples:
      >>> job_payload_key("append", "day").endswith(":payload:append:day")
      True
    """
    kind_text = str(kind or "").strip()
    ident = str(identity or "").strip()
    if not kind_text or not ident:
        raise ValueError("kind and identity are required for a payload key")
    return "hps:job:payload:%s:%s" % (kind_text, ident)


def parse_job_lease_key(key: str) -> tuple[str, str] | None:
    """
    Parse kind and identity from a lease key.

    Args:
      key (str): Lease key.

    Returns:
      tuple[str, str] | None: (kind, identity), or None when the key
      does not match.

    Examples:
      >>> parse_job_lease_key(job_lease_key("ingest", "p"))
      ('ingest', 'p')
    """
    prefix = "hps:job:lease:"
    text = str(key or "")
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix):]
    kind, sep, ident = rest.partition(":")
    if not sep or kind not in JOB_KINDS_ALL or not ident:
        return None
    return (kind, ident)


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
    raise ValueError("band must be 'hot' or 'catchup', got %r" % (band,))


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
    digest = hashlib.sha1(str(identity).encode("utf-8")).digest()
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
      >>> encode_ingest_score(band="hot", day=d, today=d, identity="a") < CATCHUP_SCORE_BASE
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
    raise ValueError("band must be 'hot' or 'catchup', got %r" % (band,))


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
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as fh:
            boot = fh.read().strip().replace("-", "")[:16]
    except OSError:
        boot = ""
    if not boot:
        try:
            boot = str(int(time.time() - time.monotonic()))
        except (OSError, ValueError, OverflowError):
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
    return "%s:%s:%s:%s" % (
        secrets.token_hex(16),
        str(host or "unknown").replace(":", "_").replace("|", "_"),
        str(boot or "unknown").replace(":", "_").replace("|", "_"),
        owner_pid,
    )


def zadd_ingest_job(
    store: SyncTimedbJobStore,
    *,
    identity: str,
    score: float | int,
    fingerprint: str | None = None,
) -> int:
    """
    Queue an ingest identity onto the in-process ingest map.

    Args:
      store (SyncTimedbJobStore): In-process job store.
      identity (str): Ingest member identity.
      score (float | int): Band-encoded score.
      fingerprint (str | None): Optional size/mtime payload.

    Returns:
      int: 1 when newly queued, else 0.

    Examples:
      >>> store = SyncTimedbJobStore("/tmp/empty")
      >>> zadd_ingest_job(store, identity="/a", score=0)
      1
    """
    return store.zadd_ingest(str(identity), float(score), fingerprint)


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
) -> Optional[ClaimedJob]:
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
) -> Optional[ClaimedJob]:
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
      ...   band="catchup", day=d, today=d, identity="a",
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
    except (ValueError, OverflowError):
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
    return "%s|%s" % (int(size), int(mtime_ns))


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
      ...   store, kind="ingest", identity="/a", fingerprint="1|2",
      ... )
    """
    if not fingerprint:
        return
    with store._lock:
        store._payloads.setdefault((str(kind), str(identity)), {})[
            "fingerprint"
        ] = str(fingerprint)


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
      ...   SyncTimedbJobStore(""), kind="ingest", identity="/a",
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
      ...   SyncTimedbJobStore(""), band="hot", owner_token="n:h:b:1",
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


def ingest_zset_census(store: SyncTimedbJobStore) -> tuple[int, int, int]:
    """
    Return (hot_queued, catchup_queued, total) for the ingest map.

    Args:
      store (SyncTimedbJobStore): In-process job store.

    Returns:
      tuple[int, int, int]: Hot depth, catchup depth, and total queued.

    Examples:
      >>> ingest_zset_census(SyncTimedbJobStore(""))
      (0, 0, 0)
    """
    if store is None:
        return (0, 0, 0)
    zkey = job_queue_key(JOB_KIND_INGEST)
    hot_lo, hot_hi = ingest_score_range("hot")
    catch_lo, catch_hi = ingest_score_range("catchup")
    return (
        store.zcount(zkey, hot_lo, hot_hi),
        store.zcount(zkey, catch_lo, catch_hi),
        store.zcard(zkey),
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
    if str(kind) == JOB_KIND_INGEST:
        return store.zcard(job_queue_key(kind))
    return store.llen(job_queue_key(kind))


def queue_census(store: SyncTimedbJobStore) -> Dict[str, Dict[str, int]]:
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
    out: Dict[str, Dict[str, int]] = {}
    for kind in JOB_KINDS_ALL:
        out[kind] = {
            "queued": queue_depth(store, kind=kind),
            "inflight": store.hlen(job_inflight_key(kind)),
        }
    return out


def format_queue_census(census: Dict[str, Dict[str, int]]) -> str:
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
            "%s=%d/%d"
            % (kind, entry.get("inflight", 0), entry.get("queued", 0)),
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
    except (TypeError, ValueError):
        return 0


def read_inflight_entry(
    store: SyncTimedbJobStore,
    *,
    kind: str,
    identity: str,
) -> Optional[Tuple[float, str, Optional[float]]]:
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
      ...   SyncTimedbJobStore(""), kind="ingest", identity="a",
      ... ) is None
      True
    """
    with store._lock:
        return store._inflight.get(str(kind), {}).get(str(identity))


def read_inflight_entries(
    store: SyncTimedbJobStore,
    *,
    kind: str,
) -> Dict[str, Tuple[float, str, Optional[float]]]:
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


def count_inflight_by_band(store: SyncTimedbJobStore) -> Tuple[int, int]:
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
        store, kind=JOB_KIND_INGEST,
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
    Record a job that exhausted its retries so it is not silently dropped.

    Args:
      archive_data_dir (str): Archive data directory root.
      kind (str): Job kind.
      identity (str): Job identity.
      attempt (int): Attempt count at give-up time.
      reason (str): Short failure reason.
      max_entries (int): Cap on retained entries (oldest trimmed first).

    Returns:
      bool: True when the entry was persisted.

    Examples:
      >>> append_queue_dead_letter(
      ...   "/nonexistent-dir", kind="ingest", identity="x", attempt=9,
      ...   reason="boom",
      ... )
      False
    """
    path = queue_dead_letter_path(archive_data_dir)
    entries = load_persistence_document(
        path, QUEUE_DEAD_LETTER_KIND, default=[],
    )
    if not isinstance(entries, list):
        entries = []
    entries.append({
        "kind": str(kind),
        "identity": str(identity),
        "attempt": int(attempt),
        "reason": str(reason)[:500],
        "recorded_at": time.time(),
    })
    if len(entries) > max_entries:
        entries = entries[-max_entries:]
    try:
        save_persistence_document(path, QUEUE_DEAD_LETTER_KIND, entries)
    except OSError:
        return False
    return True


def identity_in_queue_dead_letter(
    archive_data_dir: str,
    *,
    kind: str,
    identity: str,
) -> bool:
    """
    Return True when kind/identity is already in the queue dead-letter.

    Args:
      archive_data_dir (str): Archive data directory root.
      kind (str): Job kind.
      identity (str): Job identity.

    Returns:
      bool: True when a matching dead-letter entry exists.

    Examples:
      >>> identity_in_queue_dead_letter("/nope", kind="ingest", identity="x")
      False
    """
    if not archive_data_dir or not identity:
        return False
    path = queue_dead_letter_path(archive_data_dir)
    entries = load_persistence_document(
        path, QUEUE_DEAD_LETTER_KIND, default=[],
    )
    if not isinstance(entries, list):
        return False
    want_kind = str(kind)
    want_id = str(identity)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("kind") or "") != want_kind:
            continue
        if str(entry.get("identity") or "") == want_id:
            return True
    return False


def _score_arg(value: float) -> str:
    """
    Render a score bound as a range argument.

    Args:
      value (float): Finite score, -inf, or +inf.

    Returns:
      str: -inf, +inf, or the integer score as text.

    Examples:
      >>> _score_arg(float("-inf"))
      '-inf'
      >>> _score_arg(7.0)
      '7'
    """
    if value == float("-inf"):
        return "-inf"
    if value == float("+inf"):
        return "+inf"
    return str(int(value))


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
    pid: Optional[int] = None
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
      ...   LeaseOwner("n", "h", "b", 1), hostname="h", boot_id="b",
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
    return (
        owner.hostname == str(host).replace(":", "_").replace("|", "_")
        and owner.boot_id == str(boot).replace(":", "_").replace("|", "_")
    )


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
      ...   SyncTimedbJobStore(""), kind="ingest", identity="x",
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
) -> List[str]:
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
    out: List[str] = []
    with store._lock:
        items = list(store._inflight.get(kind, {}).items())
        for ident, (deadline, owner, score) in items:
            if len(out) >= want:
                break
            if float(deadline) > cutoff:
                continue
            store._inflight[kind].pop(ident, None)
            store._leases.pop((kind, ident), None)
            if str(kind) == JOB_KIND_INGEST:
                restore = CATCHUP_SCORE_BASE if score is None else float(score)
                store._ingest[ident] = restore + float(
                    LEASE_CONFLICT_SCORE_PENALTY,
                )
            else:
                store._lists[kind].append(ident)
                store._pending[kind].add(ident)
            out.append(ident)
            store._dirty = True
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
            owner, hostname=hostname, boot_id=boot_id,
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
      ...   SyncTimedbJobStore(""), kind="ingest", local_identities=(),
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
      ...   SyncTimedbJobStore(""), kind="ingest", identity="x",
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
        owner, hostname=hostname, boot_id=boot_id,
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
