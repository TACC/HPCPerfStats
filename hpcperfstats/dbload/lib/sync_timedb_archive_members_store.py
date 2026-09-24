"""
In-process daily archive member maps with disk sidecars and single-flight
populate.

Complete member maps and sticky day-skip flags persist under the archive
directory. Populate locks, tar-hot, append-inflight, and restore tokens stay
in memory. Day-scoped maps live on per-calendar-day shards so concurrent
days do not serialize on one RLock; the populate job queues stay
process-wide for FIFO + ingest-hot preference.

Attributes:
  ARCHIVE_MEMBERS_STORE_DIR_KIND: Persistence registry kind for the sidecar
    directory.
  ARCHIVE_MEMBERS_STORE_DIR_RELPATH: Sidecar directory basename.
  MEMBERS_DAY_SCHEMA_VERSION: Schema version written into each day file.
  SyncTimedbArchiveMembersStore: Thread-safe in-process member store.
  _DayShard: Per-calendar-day lock and maps.
  _PROCESS_STORE: Process-local store installed by the orchestrator.
  _PROCESS_STORE_LOCK: Lock covering process-local store install/get.
  _set_threading_events: Wake Events after a day-shard RLock is released.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterable, Optional

from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    artifact_path,
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.dbload.lib.sync_timedb_store_lock_timing import TimedRLock


def _set_threading_events(
    events: Iterable[threading.Event | None],
) -> None:
    """
    Wake Events collected under a day-shard RLock after release.

    Args:
      events (Iterable[threading.Event | None]): Events popped while holding
        a day lock. ``None`` entries are skipped.

    Returns:
      None

    Examples:
      >>> _set_threading_events([])
    """
    for event in events:
        if event is not None:
            event.set()


ARCHIVE_MEMBERS_STORE_DIR_KIND = "archive_members_store_dir"
ARCHIVE_MEMBERS_STORE_DIR_RELPATH = ".sync_timedb_archive_members"
MEMBERS_DAY_SCHEMA_VERSION = 1


class _DayShard:
    """
    Per-calendar-day maps and ``TimedRLock`` for the members store.

    The enclosing store holds a process lock only for the shard table.
    Day-scoped mutate and lookup take this shard lock so distinct days
    do not serialize.

    Attributes:
      day_token: ISO calendar day for this shard.
      lock: Day-local re-entrant lock (``members_store_day`` telem kind).
      members: Identity to member-name/size map.
      complete: Identities marked complete.
      day_skip: Sticky skip payload, or None when unset.
      degraded: True when populate-degraded is set.
      dedupe_hint: True when the day saw duplicate tar members.
      events: Populate completion events by identity.
      populate_owner: Thread ident of the populate owner by identity.
      tar_hot: In-memory ingest-tar-hot reason, or empty.
      append_inflight: True when an append is in flight.
      restore: In-memory restore owner token, or empty.
      populate_source: Ephemeral populate-source token by canonical path.
    """

    def __init__(self, day_token: str) -> None:
        """
        Create an empty day shard.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> _DayShard("2026-01-01").day_token
          '2026-01-01'
        """
        self.day_token = str(day_token)
        self.lock = TimedRLock("members_store_day")
        self.members: Dict[str, Dict[str, int]] = {}
        self.complete: Dict[str, bool] = {}
        self.day_skip: Dict[str, str] | None = None
        self.degraded: bool = False
        self.dedupe_hint: bool = False
        self.events: Dict[str, threading.Event] = {}
        self.populate_owner: Dict[str, int] = {}
        self.tar_hot: str = ""
        self.append_inflight: bool = False
        self.restore: str = ""
        self.populate_source: Dict[str, str] = {}


class SyncTimedbArchiveMembersStore:
    """
    Thread-safe member maps keyed by calendar day and archive identity.

    One populate owner per identity; waiters block on an Event until the
    owner stores a complete map or a sticky skip. Day-scoped state lives
    on ``_DayShard`` instances; populate job queues remain process-wide.

    Attributes:
      archive_dir: Archive data directory that owns the sidecar directory.
      _lock: Process-level lock for populate queues (compat alias).
      _populate_jobs_hot: Ingest-hot populate jobs (deque; not persisted).
      _populate_jobs_cold: Cold populate jobs (deque; not persisted).
      _populate_queued: Calendar days already queued for populate.
      _populate_cv: Condition used by populate waiters and owners.
      _shards: Day token to ``_DayShard``.
      _shards_lock: Lock covering create/lookup of ``_shards`` only.
    """

    def __init__(self, archive_dir: str) -> None:
        """
        Create an empty store and reload durable day sidecars.

        Args:
          archive_dir (str): Archive data directory root.

        Returns:
          None

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").is_complete(
          ...   "2026-01-01", "id",
          ... )
          False
        """
        self.archive_dir = str(archive_dir)
        self._shards_lock = threading.Lock()
        self._shards: Dict[str, _DayShard] = {}
        # Populate FIFO + ingest-hot preference stays process-wide.
        self._lock = TimedRLock("members_store")
        self._populate_jobs_hot: Deque[Any] = deque()
        self._populate_jobs_cold: Deque[Any] = deque()
        self._populate_queued: set[str] = set()
        self._populate_cv = threading.Condition(self._lock)
        if self.archive_dir:
            os.makedirs(self._store_dir(), exist_ok=True)
            self.load()

    def _shard(self, day_token: str) -> _DayShard:
        """
        Return the day shard, creating it under the shard-table lock.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          _DayShard: Day-local lock and maps.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/a")._shard(
          ...   "2026-01-01",
          ... ).day_token
          '2026-01-01'
        """
        day = str(day_token)
        with self._shards_lock:
            shard = self._shards.get(day)
            if shard is None:
                shard = _DayShard(day)
                self._shards[day] = shard
            return shard

    def _day_lock(self, day_token: str) -> TimedRLock:
        """
        Return the TimedRLock for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          TimedRLock: Day-shard lock.

        Examples:
          >>> isinstance(
          ...   SyncTimedbArchiveMembersStore("/tmp/a")._day_lock("d"),
          ...   TimedRLock,
          ... )
          True
        """
        return self._shard(day_token).lock

    def _store_dir(self) -> str:
        """
        Return the registered sidecar directory for member day files.

        Returns:
          str: Absolute directory path.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/a")._store_dir().endswith(
          ...   ".sync_timedb_archive_members",
          ... )
          True
        """
        return artifact_path(self.archive_dir, ARCHIVE_MEMBERS_STORE_DIR_KIND)

    def _day_path(self, day_token: str) -> str:
        """
        Return the JSON sidecar path for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          str: Absolute JSON path.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/a")._day_path(
          ...   "2026-01-01",
          ... ).endswith("2026-01-01.json")
          True
        """
        return os.path.join(self._store_dir(), "%s.json" % day_token)

    def _event_locked(
        self,
        shard: _DayShard,
        identity: str,
    ) -> threading.Event:
        """
        Return the populate Event for one identity on a held day shard.

        Args:
          shard (_DayShard): Day shard (caller holds ``shard.lock``).
          identity (str): Archive identity suffix.

        Returns:
          threading.Event: Shared completion event.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/a")
          >>> shard = store._shard("d")
          >>> with shard.lock:
          ...   isinstance(store._event_locked(shard, "i"), threading.Event)
          True
        """
        key = str(identity)
        event = shard.events.get(key)
        if event is None:
            event = threading.Event()
            shard.events[key] = event
        return event

    def _wake_and_drop_event_locked(
        self,
        shard: _DayShard,
        identity: str,
    ) -> Optional[threading.Event]:
        """
        Drop the waiter Event for one identity; caller sets it after unlock.

        Args:
          shard (_DayShard): Day shard (caller holds ``shard.lock``).
          identity (str): Archive identity suffix.

        Returns:
          threading.Event | None: Popped Event, or None when none was stored.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> shard = store._shard("2026-01-01")
          >>> with shard.lock:
          ...   store._wake_and_drop_event_locked(shard, "id") is None
          True
        """
        return shard.events.pop(str(identity), None)

    def _drop_stale_identities_locked(
        self,
        shard: _DayShard,
        keep_identity: str,
    ) -> list[threading.Event]:
        """
        Drop abandoned sibling identities after the live identity completes.

        Identity drift (T1→T2) otherwise leaves Events and incomplete maps
        for the rest of the supervisor life. Caller sets popped Events after
        releasing the day-shard RLock.

        Args:
          shard (_DayShard): Day shard (caller holds ``shard.lock``).
          keep_identity (str): Identity that just became complete.

        Returns:
          list[threading.Event]: Events to wake after the lock is released.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> shard = store._shard("2026-01-01")
          >>> with shard.lock:
          ...   store._drop_stale_identities_locked(shard, "id")
          []
        """
        keep = str(keep_identity)
        events: list[threading.Event] = []
        for identity in list(shard.members):
            if identity != keep and identity not in shard.populate_owner:
                shard.members.pop(identity, None)
                shard.complete.pop(identity, None)
        for identity in list(shard.events):
            if identity != keep and identity not in shard.populate_owner:
                events.append(shard.events.pop(identity))
        return events

    def try_begin_populate(self, day_token: str, identity: str) -> bool:
        """
        Become the single populate owner for one archive identity.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          bool: True when this thread should scan; False when it must wait.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.try_begin_populate("2026-01-01", "id")
          True
        """
        ident = str(identity)
        shard = self._shard(day_token)
        with shard.lock:
            if ident in shard.populate_owner:
                return False
            if shard.complete.get(ident):
                return False
            shard.populate_owner[ident] = threading.get_ident()
            self._event_locked(shard, ident).clear()
            return True

    def finish_populate(
        self,
        day_token: str,
        identity: str,
        *,
        members: Dict[str, int] | None = None,
        complete: bool = False,
    ) -> None:
        """
        Publish populate results and wake waiters.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.
          members (dict[str, int] | None): Member name to size map.
          complete (bool): True when the map is authoritative.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.try_begin_populate("2026-01-01", "id")
          True
          >>> store.finish_populate(
          ...   "2026-01-01", "id", members={}, complete=True,
          ... )
        """
        ident = str(identity)
        normalized = None
        if members is not None:
            normalized = {
                str(name): int(size) for name, size in members.items()
            }
        events: list[threading.Event | None] = []
        shard = self._shard(day_token)
        with shard.lock:
            if normalized is not None:
                shard.members[ident] = normalized
            if complete:
                shard.complete[ident] = True
            shard.populate_owner.pop(ident, None)
            events.append(self._wake_and_drop_event_locked(shard, ident))
            if complete:
                events.extend(
                    self._drop_stale_identities_locked(shard, ident),
                )
        _set_threading_events(events)
        if complete:
            self.persist_day(day_token)

    def wait_for_complete(
        self,
        day_token: str,
        identity: str,
        *,
        timeout_s: float = 30.0,
    ) -> Optional[Dict[str, int]]:
        """
        Block until populate completes, a skip is set, or the timeout elapses.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.
          timeout_s (float): Maximum seconds to wait.

        Returns:
          dict[str, int] | None: Complete member map, or None on timeout
          or sticky skip.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").wait_for_complete(
          ...   "2026-01-01", "id", timeout_s=0.01,
          ... ) is None
          True
        """
        ident = str(identity)
        shard = self._shard(day_token)
        deadline = time.time() + float(timeout_s)
        while time.time() < deadline:
            members_ref = None
            with shard.lock:
                if shard.day_skip is not None:
                    return None
                if shard.complete.get(ident):
                    members_ref = shard.members.get(ident) or {}
                else:
                    event = self._event_locked(shard, ident)
            if members_ref is not None:
                return dict(members_ref)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            event.wait(timeout=min(0.25, remaining))
        members_ref = None
        with shard.lock:
            if shard.complete.get(ident):
                members_ref = shard.members.get(ident) or {}
        if members_ref is not None:
            return dict(members_ref)
        return None

    def store_complete(
        self,
        day_token: str,
        identity: str,
        members: Dict[str, int],
        *,
        saw_duplicates: bool = False,
    ) -> None:
        """
        Replace the durable member map for one identity and mark it complete.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.
          members (dict[str, int]): Member name to size map.
          saw_duplicates (bool): True when the tar listed duplicate names.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.store_complete("2026-01-01", "id", {"a": 1})
        """
        ident = str(identity)
        normalized = {
            str(name): int(size) for name, size in members.items()
        }
        events: list[threading.Event | None] = []
        shard = self._shard(day_token)
        with shard.lock:
            shard.members[ident] = normalized
            shard.complete[ident] = True
            if saw_duplicates:
                shard.dedupe_hint = True
            events.append(self._wake_and_drop_event_locked(shard, ident))
            events.extend(self._drop_stale_identities_locked(shard, ident))
        _set_threading_events(events)
        self.persist_day(day_token)

    def is_complete(self, day_token: str, identity: str) -> bool:
        """
        Return True when the identity has a complete durable map.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          bool: True when the map is complete.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").is_complete(
          ...   "2026-01-01", "id",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            return bool(shard.complete.get(str(identity)))

    def lookup_member(
        self,
        day_token: str,
        identity: str,
        name: str,
    ) -> Optional[int]:
        """
        Return one member size from a complete or partial map.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.
          name (str): Archive member name.

        Returns:
          int | None: Stored size, or None when missing or the day is
          sticky-skipped.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").lookup_member(
          ...   "2026-01-01", "id", "host/1",
          ... ) is None
          True
        """
        shard = self._shard(day_token)
        with shard.lock:
            if shard.day_skip is not None:
                return None
            members = shard.members.get(str(identity))
            if members is None:
                return None
            size = members.get(str(name))
            return None if size is None else int(size)

    def set_day_skip(
        self,
        day_token: str,
        *,
        kind: str,
        detail: str = "",
    ) -> None:
        """
        Persist a sticky ingest skip for one calendar day.

        Args:
          day_token (str): ISO calendar day.
          kind (str): Skip classification such as read_error.
          detail (str): Short diagnostic text.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.set_day_skip("2026-01-01", kind="read_error")
        """
        events: list[threading.Event] = []
        shard = self._shard(day_token)
        with shard.lock:
            shard.day_skip = {
                "kind": str(kind),
                "detail": str(detail),
            }
            events.extend(list(shard.events.values()))
            shard.events.clear()
        _set_threading_events(events)
        self.persist_day(day_token)

    def get_day_skip(self, day_token: str) -> Optional[Dict[str, str]]:
        """
        Return the sticky skip payload for a calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          dict[str, str] | None: kind/detail payload, or None when unset.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").get_day_skip(
          ...   "2026-01-01",
          ... ) is None
          True
        """
        shard = self._shard(day_token)
        with shard.lock:
            payload = shard.day_skip
        if payload is None:
            return None
        return dict(payload)

    def clear_day_skip(self, day_token: str) -> None:
        """
        Clear the persisted sticky skip for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_day_skip("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.day_skip = None
        self.persist_day(day_token)

    def lookup_complete_map(
        self,
        day_token: str,
        identity: str,
    ) -> Optional[Dict[str, int]]:
        """
        Return a copy of a complete member map, or None when incomplete.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          dict[str, int] | None: Complete map, or None.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").lookup_complete_map(
          ...   "2026-01-01", "id",
          ... ) is None
          True
        """
        shard = self._shard(day_token)
        with shard.lock:
            ident = str(identity)
            if not shard.complete.get(ident):
                return None
            members_ref = shard.members.get(ident) or {}
        return dict(members_ref)

    def is_fully_warm(self, day_token: str, identity: str) -> bool:
        """
        Return True when a complete non-empty member map is present.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          bool: True when complete and the map has at least one member.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").is_fully_warm(
          ...   "2026-01-01", "id",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            ident = str(identity)
            if not shard.complete.get(ident):
                return False
            members = shard.members.get(ident)
            return bool(members)

    def merge_members(
        self,
        day_token: str,
        identity: str,
        member_map: Dict[str, int],
        *,
        saw_duplicates: bool = False,
    ) -> bool:
        """
        Merge appended sizes into a complete map and persist.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.
          member_map (dict[str, int]): Newly appended member sizes.
          saw_duplicates (bool): True when the append listed duplicate names.

        Returns:
          bool: True when a complete map was updated.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").merge_members(
          ...   "2026-01-01", "id", {"a": 1},
          ... )
          True
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").merge_members(
          ...   "2026-01-01", "id", {},
          ... )
          False
        """
        if not member_map:
            return False
        ident = str(identity)
        incoming = {
            str(name): int(size) for name, size in member_map.items()
        }
        shard = self._shard(day_token)
        while True:
            with shard.lock:
                members_ref = shard.members.get(ident)
            current = dict(members_ref) if members_ref is not None else {}
            for name, size_i in incoming.items():
                prev = current.get(name)
                if prev is None or size_i > int(prev):
                    current[name] = size_i
            events: list[threading.Event | None] = []
            with shard.lock:
                if shard.members.get(ident) is not members_ref:
                    continue
                shard.members[ident] = current
                shard.complete[ident] = True
                if saw_duplicates:
                    shard.dedupe_hint = True
                shard.degraded = False
                events.append(
                    self._wake_and_drop_event_locked(shard, ident),
                )
                events.extend(
                    self._drop_stale_identities_locked(shard, ident),
                )
            _set_threading_events(events)
            self.persist_day(day_token)
            return True

    def clear_incomplete(self, day_token: str, identity: str) -> None:
        """
        Drop a non-complete identity map without touching complete peers.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_incomplete("2026-01-01", "id")
        """
        ident = str(identity)
        event = None
        shard = self._shard(day_token)
        with shard.lock:
            if shard.complete.get(ident):
                return
            shard.members.pop(ident, None)
            shard.populate_owner.pop(ident, None)
            event = self._wake_and_drop_event_locked(shard, ident)
        _set_threading_events([event])

    def set_degraded(self, day_token: str) -> None:
        """
        Persist a populate-degraded flag for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.set_degraded("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.degraded = True
        self.persist_day(day_token)

    def clear_degraded(self, day_token: str) -> None:
        """
        Clear the persisted populate-degraded flag for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_degraded("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.degraded = False
        self.persist_day(day_token)

    def is_degraded(self, day_token: str) -> bool:
        """
        Return True when the day is marked populate-degraded.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when degraded is set.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").is_degraded(
          ...   "2026-01-01",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            return bool(shard.degraded)

    def set_populate_source(self, canonical: str, source: str) -> None:
        """
        Record an ephemeral populate-source token for one canonical path.

        Args:
          canonical (str): Canonical daily archive path.
          source (str): Token such as tar_populated or sealed_populated.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.set_populate_source("/d/2026-01-01.tar", "tar_populated")
        """
        day = self._day_token_from_canonical(canonical)
        shard = self._shard(day)
        with shard.lock:
            shard.populate_source[str(canonical)] = str(source)

    def peek_populate_source(self, canonical: str) -> Optional[str]:
        """
        Return the populate-source token without consuming it.

        Args:
          canonical (str): Canonical daily archive path.

        Returns:
          str | None: Stored token, or None when unset.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").peek_populate_source(
          ...   "/d/2026-01-01.tar",
          ... ) is None
          True
        """
        day = self._day_token_from_canonical(canonical)
        shard = self._shard(day)
        with shard.lock:
            value = shard.populate_source.get(str(canonical))
            return None if value is None else str(value)

    def consume_populate_source(self, canonical: str) -> Optional[str]:
        """
        Pop the populate-source token for one canonical path.

        Args:
          canonical (str): Canonical daily archive path.

        Returns:
          str | None: Stored token, or None when unset.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").consume_populate_source(
          ...   "/d/2026-01-01.tar",
          ... ) is None
          True
        """
        day = self._day_token_from_canonical(canonical)
        shard = self._shard(day)
        with shard.lock:
            value = shard.populate_source.pop(str(canonical), None)
            return None if value is None else str(value)

    def _day_token_from_canonical(self, canonical: str) -> str:
        """
        Best-effort calendar day from a canonical archive basename.

        Args:
          canonical (str): Canonical daily archive path.

        Returns:
          str: ISO day token, or the basename stem when no date is found.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/a")._day_token_from_canonical(
          ...   "/d/2026-01-01.tar",
          ... )
          '2026-01-01'
        """
        base = os.path.basename(str(canonical))
        for suffix in (".tar.zst", ".tar.gz", ".tar"):
            if base.endswith(suffix):
                return base[: -len(suffix)]
        return base or "unknown"

    def _drop_populate_source_locked(self, shard: _DayShard) -> None:
        """
        Drop ephemeral populate-source tokens on one day shard.

        Args:
          shard (_DayShard): Day shard (caller holds ``shard.lock``).

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> shard = store._shard("2026-01-01")
          >>> with shard.lock:
          ...   store._drop_populate_source_locked(shard)
        """
        shard.populate_source.clear()

    def enqueue_populate(self, job: Any) -> bool:
        """
        Enqueue one in-process populate job, deduped per calendar day.

        Args:
          job (Any): Populate job payload with optional day_token.

        Returns:
          bool: True when the job was newly queued.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.enqueue_populate({"day_token": "2026-01-01"})
          True
        """
        day = ""
        if isinstance(job, dict):
            day = str(job.get("day_token") or "")
        hot_rank = self._populate_job_hot_rank(job)
        with self._populate_cv:
            if day and day in self._populate_queued:
                return False
            if day:
                self._populate_queued.add(day)
            self._enqueue_populate_job_locked(job, hot_rank=hot_rank)
            self._populate_cv.notify()
        return True

    def _enqueue_populate_job_locked(
        self,
        job: Any,
        *,
        hot_rank: int | None = None,
    ) -> None:
        """
        Append one populate job to the hot or cold deque by ingest-hot rank.

        Args:
          job (Any): Populate job payload.
          hot_rank (int | None): Precomputed prefer rank, or None to
            resolve from the day shard under a separate lock.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store._enqueue_populate_job_locked(
          ...   {"day_token": "x"}, hot_rank=0,
          ... )
        """
        rank = 0 if hot_rank is None else int(hot_rank)
        if rank > 0:
            self._populate_jobs_hot.append(job)
        else:
            self._populate_jobs_cold.append(job)

    def _populate_queue_empty_locked(self) -> bool:
        """
        Return True when both populate deques are empty.

        Returns:
          bool: True when no populate jobs are queued.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._populate_queue_empty_locked()
          True
        """
        return not self._populate_jobs_hot and not self._populate_jobs_cold

    def _populate_job_hot_rank(self, job: Any) -> int:
        """
        Return ingest-hot prefer rank for one queued populate job.

        Args:
          job (Any): Populate job payload.

        Returns:
          int: Prefer rank (0 when the day is not ingest-hot).

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store._populate_job_hot_rank({"day_token": "x"})
          0
        """
        if not isinstance(job, dict):
            return 0
        day = str(job.get("day_token") or "")
        if not day:
            return 0
        reason = self.ingest_tar_hot_reason(day)
        return {
            "chunk_prewarm": 3,
            "populate_wait": 2,
            "populate_enqueue": 1,
        }.get(reason, 0)

    def dequeue_populate(self, *, timeout_s: float = 1.0) -> Any:
        """
        Pop one in-process populate job, preferring ingest-hot days.

        Args:
          timeout_s (float): Seconds to block.

        Returns:
          Any: Job payload, or None on timeout.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").dequeue_populate(
          ...   timeout_s=0.01,
          ... ) is None
          True
        """
        deadline = time.time() + max(0.0, float(timeout_s))
        with self._populate_cv:
            while self._populate_queue_empty_locked():
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._populate_cv.wait(timeout=remaining)
            if self._populate_jobs_hot:
                return self._populate_jobs_hot.popleft()
            return self._populate_jobs_cold.popleft()

    def complete_populate_job(self, job: Any) -> None:
        """
        Drop the ephemeral queued-day flag after a successful populate.

        Args:
          job (Any): Finished populate job payload.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.complete_populate_job({"day_token": "2026-01-01"})
        """
        if not isinstance(job, dict):
            return
        day = str(job.get("day_token") or "")
        if not day:
            return
        with self._lock:
            self._populate_queued.discard(day)

    def requeue_populate_job(self, job: Any) -> None:
        """
        Return a failed populate job to the in-process queue.

        Args:
          job (Any): Populate job payload.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.requeue_populate_job({"day_token": "2026-01-01"})
        """
        if not isinstance(job, dict):
            return
        hot_rank = self._populate_job_hot_rank(job)
        with self._populate_cv:
            self._enqueue_populate_job_locked(job, hot_rank=hot_rank)
            self._populate_cv.notify()

    def invalidate(self, day_token: str, identity: str) -> None:
        """
        Drop one identity map without touching the job-store snapshot.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.invalidate("2026-01-01", "id")
        """
        ident = str(identity)
        event = None
        shard = self._shard(day_token)
        with shard.lock:
            shard.members.pop(ident, None)
            shard.complete.pop(ident, None)
            shard.populate_owner.pop(ident, None)
            self._drop_populate_source_locked(shard)
            event = self._wake_and_drop_event_locked(shard, ident)
        _set_threading_events([event])
        self.persist_day(day_token)

    def invalidate_all(self) -> None:
        """
        Drop every durable member sidecar under the archive directory.

        Job-store snapshots are left untouched.

        Returns:
          None

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").invalidate_all()
        """
        events: list[threading.Event] = []
        with self._shards_lock:
            shards = list(self._shards.values())
            self._shards.clear()
        for shard in shards:
            with shard.lock:
                events.extend(list(shard.events.values()))
                shard.events.clear()
                shard.members.clear()
                shard.complete.clear()
                shard.day_skip = None
                shard.degraded = False
                shard.dedupe_hint = False
                shard.populate_owner.clear()
                shard.tar_hot = ""
                shard.append_inflight = False
                shard.restore = ""
                shard.populate_source.clear()
        with self._populate_cv:
            self._populate_jobs_hot.clear()
            self._populate_jobs_cold.clear()
            self._populate_queued.clear()
            self._populate_cv.notify_all()
        _set_threading_events(events)
        store_dir = self._store_dir()
        if not os.path.isdir(store_dir):
            return
        for name in os.listdir(store_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(store_dir, name)
            try:
                os.unlink(path)
            except OSError:
                continue

    def set_ingest_tar_hot(self, day_token: str, *, reason: str) -> None:
        """
        Mark a calendar day as ingest-tar-hot in memory only.

        Args:
          day_token (str): ISO calendar day.
          reason (str): Hot reason such as populate or chunk_prewarm.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.set_ingest_tar_hot("2026-01-01", reason="populate")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.tar_hot = str(reason)

    def ingest_tar_hot_reason(self, day_token: str) -> str:
        """
        Return the in-memory ingest-tar-hot reason, or empty when unset.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          str: Reason string, or empty.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").ingest_tar_hot_reason(
          ...   "2026-01-01",
          ... )
          ''
        """
        shard = self._shard(day_token)
        with shard.lock:
            return str(shard.tar_hot)

    def clear_ingest_tar_hot(self, day_token: str) -> None:
        """
        Drop the in-memory ingest-tar-hot flag for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_ingest_tar_hot("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.tar_hot = ""

    def ingest_tar_hot(self, day_token: str) -> bool:
        """
        Return True when the day is marked ingest-tar-hot.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when a hot reason is set.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").ingest_tar_hot(
          ...   "2026-01-01",
          ... )
          False
        """
        return bool(self.ingest_tar_hot_reason(day_token))

    def set_append_inflight(self, day_token: str) -> None:
        """
        Mark an in-memory append in flight for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.set_append_inflight("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.append_inflight = True

    def clear_append_inflight(self, day_token: str) -> None:
        """
        Drop the in-memory append-inflight flag for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_append_inflight("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.append_inflight = False

    def append_inflight(self, day_token: str) -> bool:
        """
        Return True when an append is in flight for the day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when the in-memory append flag is set.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").append_inflight(
          ...   "2026-01-01",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            return bool(shard.append_inflight)

    def persist_day(self, day_token: str) -> None:
        """
        Write one calendar day's durable maps to the sidecar directory.

        Snapshot identity refs and cheap flags under the day lock, then
        copy giant member maps and persist after the lock is released.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").persist_day(
          ...   "2026-01-01",
          ... )
        """
        day = str(day_token)
        shard = self._shard(day)
        with shard.lock:
            snapshots = tuple(
                (identity, members)
                for identity, members in shard.members.items()
                if shard.complete.get(identity)
            )
            skip = shard.day_skip
            if isinstance(skip, dict):
                skip = dict(skip)
            degraded = bool(shard.degraded)
            dedupe = bool(shard.dedupe_hint)
        payload = {
            "schema_version": MEMBERS_DAY_SCHEMA_VERSION,
            "day_token": day,
            "identities": {
                identity: {"members": dict(members), "complete": True}
                for identity, members in snapshots
            },
            "day_skip": skip,
            "degraded": degraded,
            "dedupe_hint": dedupe,
        }
        path = self._day_path(day)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_persistence_document(path, "archive_members_day", payload)

    def load(self) -> None:
        """
        Reload durable day sidecars from the registered directory.

        Returns:
          None

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").load()
        """
        store_dir = self._store_dir()
        if not os.path.isdir(store_dir):
            return
        for name in os.listdir(store_dir):
            if not name.endswith(".json"):
                continue
            path = os.path.join(store_dir, name)
            raw = load_persistence_document(
                path, "archive_members_day", default={},
            )
            if not isinstance(raw, dict):
                continue
            day = str(raw.get("day_token") or name[:-5])
            identities = raw.get("identities") or {}
            skip = raw.get("day_skip")
            skip_payload = None
            if isinstance(skip, dict) and skip.get("kind"):
                skip_payload = {
                    "kind": str(skip.get("kind")),
                    "detail": str(skip.get("detail") or ""),
                }
            members_ready: Dict[str, Dict[str, int]] = {}
            if isinstance(identities, dict):
                for identity, body in identities.items():
                    if not isinstance(body, dict) or not body.get("complete"):
                        continue
                    members = body.get("members") or {}
                    members_ready[str(identity)] = {
                        str(member): int(size)
                        for member, size in members.items()
                    }
            shard = self._shard(day)
            with shard.lock:
                if skip_payload is not None:
                    shard.day_skip = skip_payload
                if raw.get("degraded"):
                    shard.degraded = True
                if raw.get("dedupe_hint"):
                    shard.dedupe_hint = True
                shard.members.update(members_ready)
                for identity in members_ready:
                    shard.complete[identity] = True

    def try_acquire_restore(self, day_token: str, token: str) -> bool:
        """
        Become the in-memory restore owner for one calendar day.

        Args:
          day_token (str): ISO calendar day.
          token (str): Owner token.

        Returns:
          bool: True when this caller now owns restore.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.try_acquire_restore("2026-01-01", "t1")
          True
        """
        day = str(day_token)
        if not day or day == "unknown":
            return False
        shard = self._shard(day)
        with shard.lock:
            current = shard.restore
            if current and current != str(token):
                return False
            shard.restore = str(token)
            return True

    def renew_restore(self, day_token: str, token: str) -> bool:
        """
        Refresh an owned in-memory restore token.

        Args:
          day_token (str): ISO calendar day.
          token (str): Owner token.

        Returns:
          bool: True when this caller still owns restore.

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.try_acquire_restore("2026-01-01", "t1")
          True
          >>> store.renew_restore("2026-01-01", "t1")
          True
        """
        shard = self._shard(day_token)
        with shard.lock:
            return shard.restore == str(token)

    def clear_restore(self, day_token: str, token: str | None = None) -> None:
        """
        Drop the in-memory restore token when the owner matches.

        Args:
          day_token (str): ISO calendar day.
          token (str | None): Owner token, or None to force-clear.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_restore("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            if token is None or shard.restore == str(token):
                shard.restore = ""

    def restore_in_progress(self, day_token: str) -> bool:
        """
        Return True when an in-memory restore token is set.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when restore is owned.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").restore_in_progress(
          ...   "2026-01-01",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            return bool(shard.restore)

    def restore_reason(self, day_token: str) -> str:
        """
        Return the in-memory restore token, or empty when unset.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          str: Owner token, or empty.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").restore_reason(
          ...   "2026-01-01",
          ... )
          ''
        """
        shard = self._shard(day_token)
        with shard.lock:
            return str(shard.restore)

    def dedupe_hint_is_set(self, day_token: str) -> bool:
        """
        Return True when the day saw duplicate tar members.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when the durable dedupe hint is set.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").dedupe_hint_is_set(
          ...   "2026-01-01",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            return bool(shard.dedupe_hint)

    def clear_dedupe_hint(self, day_token: str) -> None:
        """
        Clear the persisted dedupe hint for one calendar day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store.clear_dedupe_hint("2026-01-01")
        """
        shard = self._shard(day_token)
        with shard.lock:
            shard.dedupe_hint = False
        self.persist_day(day_token)

    def list_dedupe_hint_days(self) -> list[str]:
        """
        Return calendar days that currently have a dedupe hint.

        Returns:
          list[str]: Day tokens.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").list_dedupe_hint_days()
          []
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        days: list[str] = []
        for day, shard in shards:
            with shard.lock:
                if shard.dedupe_hint:
                    days.append(day)
        return sorted(days)

    def complete_identity_count(self, day_token: str) -> int:
        """
        Return how many identities are marked complete for one day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          int: Count of complete identities.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").complete_identity_count(
          ...   "2026-01-01",
          ... )
          0
        """
        shard = self._shard(day_token)
        with shard.lock:
            return sum(1 for flag in shard.complete.values() if flag)

    def populate_owner_active(self, day_token: str) -> bool:
        """
        Return True when any populate owner is set for the day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when a populate owner thread holds this day.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").populate_owner_active(
          ...   "2026-01-01",
          ... )
          False
        """
        shard = self._shard(day_token)
        with shard.lock:
            return bool(shard.populate_owner)

    def any_complete_identity(self, day_token: str) -> bool:
        """
        Return True when at least one identity is complete for the day.

        Args:
          day_token (str): ISO calendar day.

        Returns:
          bool: True when any complete flag is set.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty").any_complete_identity(
          ...   "2026-01-01",
          ... )
          False
        """
        return self.complete_identity_count(day_token) > 0

    # --- Test/compat aggregators (flat (day, identity) views) ---

    @property
    def _events(self) -> Dict[tuple[str, str], threading.Event]:
        """
        Aggregate populate Events across day shards for tests.

        Returns:
          dict[tuple[str, str], threading.Event]: Flat event map.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._events
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[tuple[str, str], threading.Event] = {}
        for day, shard in shards:
            with shard.lock:
                for identity, event in shard.events.items():
                    out[(day, identity)] = event
        return out

    @property
    def _members(self) -> Dict[tuple[str, str], Dict[str, int]]:
        """
        Aggregate member maps across day shards for tests.

        Returns:
          dict[tuple[str, str], dict[str, int]]: Flat member map.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._members
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[tuple[str, str], Dict[str, int]] = {}
        for day, shard in shards:
            with shard.lock:
                for identity, members in shard.members.items():
                    out[(day, identity)] = members
        return out

    @property
    def _complete(self) -> Dict[tuple[str, str], bool]:
        """
        Aggregate complete flags across day shards for tests and coord.

        Returns:
          dict[tuple[str, str], bool]: Flat complete map.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._complete
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[tuple[str, str], bool] = {}
        for day, shard in shards:
            with shard.lock:
                for identity, flag in shard.complete.items():
                    out[(day, identity)] = flag
        return out

    @property
    def _populate_owner(self) -> Dict[tuple[str, str], int]:
        """
        Aggregate populate owners across day shards for tests and coord.

        Returns:
          dict[tuple[str, str], int]: Flat owner map.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._populate_owner
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[tuple[str, str], int] = {}
        for day, shard in shards:
            with shard.lock:
                for identity, owner in shard.populate_owner.items():
                    out[(day, identity)] = owner
        return out

    @property
    def _tar_hot(self) -> Dict[str, str]:
        """
        Aggregate ingest-tar-hot reasons across day shards for tests.

        Returns:
          dict[str, str]: Day to reason.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._tar_hot
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[str, str] = {}
        for day, shard in shards:
            with shard.lock:
                if shard.tar_hot:
                    out[day] = shard.tar_hot
        return out

    @property
    def _append_inflight(self) -> Dict[str, bool]:
        """
        Aggregate append-inflight flags across day shards for tests.

        Returns:
          dict[str, bool]: Day to flag.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._append_inflight
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[str, bool] = {}
        for day, shard in shards:
            with shard.lock:
                if shard.append_inflight:
                    out[day] = True
        return out

    @property
    def _day_skip(self) -> Dict[str, Dict[str, str]]:
        """
        Aggregate sticky day-skip payloads across day shards for tests.

        Returns:
          dict[str, dict[str, str]]: Day to skip payload.

        Examples:
          >>> SyncTimedbArchiveMembersStore("/tmp/empty")._day_skip
          {}
        """
        with self._shards_lock:
            shards = list(self._shards.items())
        out: Dict[str, Dict[str, str]] = {}
        for day, shard in shards:
            with shard.lock:
                if shard.day_skip is not None:
                    out[day] = shard.day_skip
        return out


_PROCESS_STORE: SyncTimedbArchiveMembersStore | None = None
_PROCESS_STORE_LOCK = threading.Lock()


def set_process_archive_members_store(
    store: SyncTimedbArchiveMembersStore | None,
) -> None:
    """
    Install the process-wide archive members store.

    Args:
      store (SyncTimedbArchiveMembersStore | None): Store instance, or None.

    Returns:
      None

    Examples:
      >>> set_process_archive_members_store(None)
    """
    global _PROCESS_STORE
    with _PROCESS_STORE_LOCK:
        _PROCESS_STORE = store


def get_process_archive_members_store() -> SyncTimedbArchiveMembersStore | None:
    """
    Return the process-wide archive members store, or None when unset.

    Returns:
      SyncTimedbArchiveMembersStore | None: Installed store.

    Examples:
      >>> get_process_archive_members_store() is None
      True
    """
    with _PROCESS_STORE_LOCK:
        return _PROCESS_STORE


def require_process_archive_members_store() -> SyncTimedbArchiveMembersStore:
    """
    Return the process-wide store, or raise when it is not installed.

    Returns:
      SyncTimedbArchiveMembersStore: Installed store.

    Raises:
      RuntimeError: No process-wide store has been installed.

    Examples:
      >>> require_process_archive_members_store()  # doctest: +SKIP
    """
    store = get_process_archive_members_store()
    if store is None:
        raise RuntimeError("archive members store is not installed")
    return store
