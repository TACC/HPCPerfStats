"""
In-process daily archive member maps with disk sidecars and single-flight
populate.

Complete member maps and sticky day-skip flags persist under the archive
directory. Populate locks, tar-hot, append-inflight, and restore tokens stay
in memory.

Attributes:
  ARCHIVE_MEMBERS_STORE_DIR_KIND: Persistence registry kind for the sidecar
    directory.
  ARCHIVE_MEMBERS_STORE_DIR_RELPATH: Sidecar directory basename.
  MEMBERS_DAY_SCHEMA_VERSION: Schema version written into each day file.
  SyncTimedbArchiveMembersStore: Thread-safe in-process member store.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    artifact_path,
    load_persistence_document,
    save_persistence_document,
)

ARCHIVE_MEMBERS_STORE_DIR_KIND = "archive_members_store_dir"
ARCHIVE_MEMBERS_STORE_DIR_RELPATH = ".sync_timedb_archive_members"
MEMBERS_DAY_SCHEMA_VERSION = 1


class SyncTimedbArchiveMembersStore:
    """
    Thread-safe member maps keyed by calendar day and archive identity.

    One populate owner per identity; waiters block on an Event until the
    owner stores a complete map or a sticky skip.

    Attributes:
      archive_dir: Archive data directory that owns the sidecar directory.
      _append_inflight: Calendar days with an in-memory append in flight.
      _complete: Identity maps marked complete.
      _day_skip: Sticky skip payload by calendar day.
      _degraded: Calendar days marked populate-degraded.
      _dedupe_hint: Calendar days that saw duplicate tar members.
      _events: Populate completion events by (day, identity).
      _lock: Re-entrant lock covering maps and flags.
      _members: Member name to size maps by (day, identity).
      _populate_owner: Thread ident of the populate owner by (day, identity).
      _restore: In-memory restore tokens by calendar day.
      _tar_hot: In-memory ingest-tar-hot reason by calendar day.
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
        self._lock = threading.RLock()
        self._members: Dict[tuple[str, str], Dict[str, int]] = {}
        self._complete: Dict[tuple[str, str], bool] = {}
        self._day_skip: Dict[str, Dict[str, str]] = {}
        self._degraded: Dict[str, bool] = {}
        self._dedupe_hint: Dict[str, bool] = {}
        self._events: Dict[tuple[str, str], threading.Event] = {}
        self._populate_owner: Dict[tuple[str, str], int] = {}
        self._tar_hot: Dict[str, str] = {}
        self._append_inflight: Dict[str, bool] = {}
        self._restore: Dict[str, str] = {}
        if self.archive_dir:
            os.makedirs(self._store_dir(), exist_ok=True)
            self.load()

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

    def _event(self, day_token: str, identity: str) -> threading.Event:
        """
        Return the populate Event for one identity, creating it if needed.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          threading.Event: Shared completion event.

        Examples:
          >>> isinstance(
          ...   SyncTimedbArchiveMembersStore("/tmp/a")._event("d", "i"),
          ...   threading.Event,
          ... )
          True
        """
        key = (str(day_token), str(identity))
        event = self._events.get(key)
        if event is None:
            event = threading.Event()
            self._events[key] = event
        return event

    def _wake_and_drop_event_locked(
        self,
        day_token: str,
        identity: str,
    ) -> None:
        """
        Wake waiters for one identity and drop the Event once unused.

        Args:
          day_token (str): ISO calendar day.
          identity (str): Archive identity suffix.

        Returns:
          None

        Examples:
          >>> store = SyncTimedbArchiveMembersStore("/tmp/empty")
          >>> store._wake_and_drop_event_locked("2026-01-01", "id")
        """
        event = self._events.pop((str(day_token), str(identity)), None)
        if event is not None:
            event.set()

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
        key = (str(day_token), str(identity))
        with self._lock:
            if key in self._populate_owner:
                return False
            if self._complete.get(key):
                return False
            self._populate_owner[key] = threading.get_ident()
            self._event(day_token, identity).clear()
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
        key = (str(day_token), str(identity))
        with self._lock:
            if members is not None:
                self._members[key] = {
                    str(name): int(size) for name, size in members.items()
                }
            if complete:
                self._complete[key] = True
            self._populate_owner.pop(key, None)
            self._wake_and_drop_event_locked(day_token, identity)
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
        key = (str(day_token), str(identity))
        deadline = time.time() + float(timeout_s)
        while time.time() < deadline:
            with self._lock:
                if day_token in self._day_skip:
                    return None
                if self._complete.get(key):
                    return dict(self._members.get(key) or {})
                event = self._event(day_token, identity)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            event.wait(timeout=min(0.25, remaining))
        with self._lock:
            if self._complete.get(key):
                return dict(self._members.get(key) or {})
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
        key = (str(day_token), str(identity))
        with self._lock:
            self._members[key] = {
                str(name): int(size) for name, size in members.items()
            }
            self._complete[key] = True
            if saw_duplicates:
                self._dedupe_hint[str(day_token)] = True
            self._wake_and_drop_event_locked(day_token, identity)
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
        with self._lock:
            return bool(self._complete.get((str(day_token), str(identity))))

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
        with self._lock:
            if str(day_token) in self._day_skip:
                return None
            members = self._members.get((str(day_token), str(identity)))
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
        with self._lock:
            self._day_skip[str(day_token)] = {
                "kind": str(kind),
                "detail": str(detail),
            }
            day = str(day_token)
            for key in list(self._events):
                if key[0] == day:
                    event = self._events.pop(key)
                    event.set()
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
        with self._lock:
            payload = self._day_skip.get(str(day_token))
            return None if payload is None else dict(payload)

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
        key = (str(day_token), str(identity))
        with self._lock:
            self._members.pop(key, None)
            self._complete.pop(key, None)
            self._populate_owner.pop(key, None)
            self._wake_and_drop_event_locked(day_token, identity)
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
        with self._lock:
            self._members.clear()
            self._complete.clear()
            self._day_skip.clear()
            self._degraded.clear()
            self._dedupe_hint.clear()
            self._populate_owner.clear()
            for event in self._events.values():
                event.set()
            self._events.clear()
            self._tar_hot.clear()
            self._append_inflight.clear()
            self._restore.clear()
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
        with self._lock:
            self._tar_hot[str(day_token)] = str(reason)

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
        with self._lock:
            self._tar_hot.pop(str(day_token), None)

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
        with self._lock:
            return str(day_token) in self._tar_hot

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
        with self._lock:
            self._append_inflight[str(day_token)] = True

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
        with self._lock:
            self._append_inflight.pop(str(day_token), None)

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
        with self._lock:
            return bool(self._append_inflight.get(str(day_token)))

    def persist_day(self, day_token: str) -> None:
        """
        Write one calendar day's durable maps to the sidecar directory.

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
        with self._lock:
            identities: Dict[str, Any] = {}
            for (stored_day, identity), members in self._members.items():
                if stored_day != day:
                    continue
                identities[identity] = {
                    "members": dict(members),
                    "complete": bool(self._complete.get((day, identity))),
                }
            payload = {
                "schema_version": MEMBERS_DAY_SCHEMA_VERSION,
                "day_token": day,
                "identities": identities,
                "day_skip": self._day_skip.get(day),
                "degraded": bool(self._degraded.get(day)),
                "dedupe_hint": bool(self._dedupe_hint.get(day)),
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
            with self._lock:
                if isinstance(skip, dict) and skip.get("kind"):
                    self._day_skip[day] = {
                        "kind": str(skip.get("kind")),
                        "detail": str(skip.get("detail") or ""),
                    }
                if raw.get("degraded"):
                    self._degraded[day] = True
                if raw.get("dedupe_hint"):
                    self._dedupe_hint[day] = True
                if isinstance(identities, dict):
                    for identity, body in identities.items():
                        if not isinstance(body, dict):
                            continue
                        key = (day, str(identity))
                        members = body.get("members") or {}
                        self._members[key] = {
                            str(member): int(size)
                            for member, size in members.items()
                        }
                        self._complete[key] = bool(body.get("complete"))
