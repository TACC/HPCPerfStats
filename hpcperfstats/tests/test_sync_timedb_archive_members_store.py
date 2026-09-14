"""In-process archive-members store: single-flight populate and sticky skip."""
from __future__ import annotations

import threading
import time
from collections.abc import Mapping

import pytest

from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    PERSISTENCE_ARTIFACT_REGISTRY,
)


@pytest.mark.django_db(databases=[])
def test_single_flight_second_waiter_does_not_scan(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    scans = []
    started = threading.Event()
    release = threading.Event()

    def winner() -> None:
        assert store.try_begin_populate("2026-08-01", "id-a")
        started.set()
        release.wait(timeout=2)
        store.finish_populate(
            "2026-08-01",
            "id-a",
            members={"host/1": 10},
            complete=True,
        )
        scans.append("winner")

    def waiter() -> None:
        started.wait(timeout=2)
        assert not store.try_begin_populate("2026-08-01", "id-a")
        members = store.wait_for_complete(
            "2026-08-01", "id-a", timeout_s=2.0,
        )
        assert members == {"host/1": 10}
        scans.append("waiter")

    threads = [
        threading.Thread(target=winner),
        threading.Thread(target=waiter),
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=3)
    assert scans.count("winner") == 1
    assert scans.count("waiter") == 1


@pytest.mark.django_db(databases=[])
def test_sticky_day_skip_survives_reload(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbArchiveMembersStore(archive)
    store.set_day_skip("2026-08-02", kind="read_error", detail="zstd")
    store.persist_day("2026-08-02")
    revived = SyncTimedbArchiveMembersStore(archive)
    skip = revived.get_day_skip("2026-08-02")
    assert skip is not None
    assert skip["kind"] == "read_error"
    assert revived.lookup_member("2026-08-02", "id-a", "host/1") is None


@pytest.mark.django_db(databases=[])
def test_complete_members_round_trip_and_point_lookup(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    store.store_complete(
        "2026-08-03",
        "id-a",
        {"host/1": 11, "host/2": 22},
    )
    assert store.is_complete("2026-08-03", "id-a")
    assert store.lookup_member("2026-08-03", "id-a", "host/2") == 22
    store.invalidate("2026-08-03", "id-a")
    assert not store.is_complete("2026-08-03", "id-a")
    assert store.lookup_member("2026-08-03", "id-a", "host/2") is None


@pytest.mark.django_db(databases=[])
def test_ephemeral_flags_do_not_persist(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbArchiveMembersStore(archive)
    store.set_ingest_tar_hot("2026-08-04", reason="populate")
    store.set_append_inflight("2026-08-04")
    store.store_complete("2026-08-04", "id-a", {"host/1": 1})
    revived = SyncTimedbArchiveMembersStore(archive)
    assert revived.is_complete("2026-08-04", "id-a")
    assert not revived.ingest_tar_hot("2026-08-04")
    assert not revived.append_inflight("2026-08-04")


@pytest.mark.django_db(databases=[])
def test_invalidate_members_does_not_wipe_job_store(tmp_path):
    archive = str(tmp_path / "archive")
    jobs = SyncTimedbJobStore(archive)
    jobs.zadd_ingest("/raw/keep", 1.0)
    jobs.persist(force=True)
    members = SyncTimedbArchiveMembersStore(archive)
    members.store_complete("2026-08-05", "id-a", {"host/1": 1})
    members.invalidate("2026-08-05", "id-a")
    members.invalidate_all()
    revived_jobs = SyncTimedbJobStore(archive)
    assert "/raw/keep" in revived_jobs.ingest_identities()
    members_dir = PERSISTENCE_ARTIFACT_REGISTRY["archive_members_store_dir"]
    assert members_dir == ".sync_timedb_archive_members"


@pytest.mark.django_db(databases=[])
def test_finish_and_invalidate_drop_unused_events_and_ephemeral_flags(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert store.try_begin_populate("2026-08-06", "id-a")
    store.set_ingest_tar_hot("2026-08-06", reason="populate")
    store.set_append_inflight("2026-08-06")
    store.finish_populate(
        "2026-08-06",
        "id-a",
        members={"host/1": 1},
        complete=True,
    )
    assert not store._events
    assert not store._populate_owner
    store.clear_ingest_tar_hot("2026-08-06")
    store.clear_append_inflight("2026-08-06")
    assert not store.ingest_tar_hot("2026-08-06")
    assert not store.append_inflight("2026-08-06")
    store.invalidate("2026-08-06", "id-a")
    assert not store._events
    store.try_begin_populate("2026-08-07", "id-b")
    store.set_ingest_tar_hot("2026-08-07", reason="chunk_prewarm")
    store.set_append_inflight("2026-08-07")
    store.invalidate_all()
    assert not store._events
    assert not store._tar_hot
    assert not store._append_inflight
    assert not store._populate_owner


@pytest.mark.django_db(databases=[])
def test_populate_source_dropped_without_consume(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    canonical = "/daily/2026-08-08.tar.zst"
    store.set_populate_source(canonical, "tar_populated")
    assert store.peek_populate_source(canonical) == "tar_populated"
    store.invalidate("2026-08-08", "id-a")
    assert store.peek_populate_source(canonical) is None
    store.set_populate_source(canonical, "sealed_populated")
    store.finish_populate("2026-08-08", "id-a", members={}, complete=True)
    assert store.consume_populate_source(canonical) == "sealed_populated"
    assert store.consume_populate_source(canonical) is None
    store.set_populate_source(canonical, "tar_populated")
    store.invalidate_all()
    assert store.peek_populate_source(canonical) is None


@pytest.mark.django_db(databases=[])
def test_complete_identity_drops_stale_sibling_events_and_incomplete_maps(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert store.wait_for_complete("2026-08-10", "id-t1", timeout_s=0.01) is None
    assert ("2026-08-10", "id-t1") in store._events
    store.finish_populate(
        "2026-08-10",
        "id-t1",
        members={"host/stale": 1},
        complete=False,
    )
    assert store.try_begin_populate("2026-08-10", "id-t2")
    store.finish_populate(
        "2026-08-10",
        "id-t2",
        members={"host/1": 2},
        complete=True,
    )
    assert ("2026-08-10", "id-t1") not in store._events
    assert ("2026-08-10", "id-t1") not in store._members
    assert not store.is_complete("2026-08-10", "id-t1")
    assert store.is_complete("2026-08-10", "id-t2")
    assert store.lookup_member("2026-08-10", "id-t2", "host/1") == 2


@pytest.mark.django_db(databases=[])
def test_merge_complete_identity_drops_stale_sibling_events_and_incomplete_maps(
    tmp_path,
):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    store.store_complete("2026-08-11", "id-t2", {"host/old": 1})
    store.finish_populate(
        "2026-08-11",
        "id-t3",
        members={"host/stale2": 1},
        complete=False,
    )
    assert store.wait_for_complete("2026-08-11", "id-t3", timeout_s=0.01) is None
    assert ("2026-08-11", "id-t3") in store._events
    assert ("2026-08-11", "id-t3") in store._members
    assert store.merge_members("2026-08-11", "id-t2", {"host/new": 2})
    assert ("2026-08-11", "id-t3") not in store._events
    assert ("2026-08-11", "id-t3") not in store._members
    assert store.is_complete("2026-08-11", "id-t2")
    assert store.lookup_member("2026-08-11", "id-t2", "host/old") == 1
    assert store.lookup_member("2026-08-11", "id-t2", "host/new") == 2


@pytest.mark.django_db(databases=[])
def test_incomplete_maps_are_not_reloaded(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbArchiveMembersStore(archive)
    store.finish_populate(
        "2026-08-09",
        "id-partial",
        members={"host/1": 1},
        complete=False,
    )
    store.store_complete("2026-08-09", "id-full", {"host/2": 2})
    revived = SyncTimedbArchiveMembersStore(archive)
    assert not revived.is_complete("2026-08-09", "id-partial")
    assert revived.lookup_member("2026-08-09", "id-partial", "host/1") is None
    assert revived.is_complete("2026-08-09", "id-full")
    assert revived.lookup_member("2026-08-09", "id-full", "host/2") == 2


@pytest.mark.django_db(databases=[])
def test_degraded_survives_reload(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbArchiveMembersStore(archive)
    store.set_degraded("2026-08-10")
    revived = SyncTimedbArchiveMembersStore(archive)
    assert revived.is_degraded("2026-08-10")
    revived.clear_degraded("2026-08-10")
    again = SyncTimedbArchiveMembersStore(archive)
    assert not again.is_degraded("2026-08-10")


class _SlowMemberMap(Mapping):
    """Mapping whose iteration sleeps so persist copy can be timed vs the RLock."""

    def __init__(self, data, started, hold_s=0.4):
        self._data = dict(data)
        self._started = started
        self._hold_s = hold_s

    def __iter__(self):
        self._started.set()
        time.sleep(self._hold_s)
        return iter(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def __len__(self):
        return len(self._data)


@pytest.mark.django_db(databases=[])
def test_persist_day_copy_releases_lock(tmp_path):
    """Giant persist copy must not serialize ingest waiters on the store RLock."""
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    started = threading.Event()
    slow = _SlowMemberMap({"host/1": 11}, started, hold_s=0.5)
    with store._lock:
        store._members[("2026-09-01", "id-a")] = slow
        store._complete[("2026-09-01", "id-a")] = True
    acquired = []

    def persist() -> None:
        store.persist_day("2026-09-01")

    def waiter() -> None:
        assert started.wait(timeout=2)
        got = store._lock.acquire(timeout=0.05)
        acquired.append(got)
        if got:
            store._lock.release()

    persist_thread = threading.Thread(target=persist)
    waiter_thread = threading.Thread(target=waiter)
    persist_thread.start()
    waiter_thread.start()
    persist_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    revived = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert revived.lookup_member("2026-09-01", "id-a", "host/1") == 11


@pytest.mark.django_db(databases=[])
def test_lookup_complete_map_copy_releases_lock(tmp_path):
    """lookup_complete_map must copy the map after releasing the store RLock."""
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    started = threading.Event()
    slow = _SlowMemberMap({"host/1": 11}, started, hold_s=0.5)
    with store._lock:
        store._members[("2026-09-01", "id-a")] = slow
        store._complete[("2026-09-01", "id-a")] = True
    acquired = []

    def lookup() -> None:
        assert store.lookup_complete_map("2026-09-01", "id-a") == {"host/1": 11}

    def waiter() -> None:
        assert started.wait(timeout=2)
        got = store._lock.acquire(timeout=0.05)
        acquired.append(got)
        if got:
            store._lock.release()

    lookup_thread = threading.Thread(target=lookup)
    waiter_thread = threading.Thread(target=waiter)
    lookup_thread.start()
    waiter_thread.start()
    lookup_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]


@pytest.mark.django_db(databases=[])
def test_wait_for_complete_copy_releases_lock(tmp_path):
    """wait_for_complete success path must copy after releasing the RLock."""
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    started = threading.Event()
    slow = _SlowMemberMap({"host/1": 11}, started, hold_s=0.5)
    with store._lock:
        store._members[("2026-09-01", "id-a")] = slow
        store._complete[("2026-09-01", "id-a")] = True
    acquired = []

    def wait() -> None:
        assert store.wait_for_complete(
            "2026-09-01", "id-a", timeout_s=2.0,
        ) == {"host/1": 11}

    def waiter() -> None:
        assert started.wait(timeout=2)
        got = store._lock.acquire(timeout=0.05)
        acquired.append(got)
        if got:
            store._lock.release()

    wait_thread = threading.Thread(target=wait)
    waiter_thread = threading.Thread(target=waiter)
    wait_thread.start()
    waiter_thread.start()
    wait_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]


def _lock_hold_waiter(lock, started, timeout=0.05):
    acquired = []

    def waiter() -> None:
        assert started.wait(timeout=2)
        got = lock.acquire(timeout=timeout)
        acquired.append(got)
        if got:
            lock.release()

    return acquired, waiter


class _SlowItems(dict):
    """Dict whose items() sleeps so normalize CPU can be timed vs RLock."""

    def __init__(self, data, started, hold_s=0.4):
        super().__init__(data)
        self._started = started
        self._hold_s = hold_s

    def items(self):
        self._started.set()
        time.sleep(self._hold_s)
        return list(super().items())


class _SlowEvent(threading.Event):
    """Event whose set() sleeps so waiters can probe the store RLock."""

    def __init__(self, started, hold_s=0.4):
        super().__init__()
        self._started = started
        self._hold_s = hold_s

    def set(self):
        self._started.set()
        time.sleep(self._hold_s)
        super().set()


@pytest.mark.django_db(databases=[])
def test_finish_populate_lock_hold_normalizes_before_rlock(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert store.try_begin_populate("2026-09-01", "id-a")
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)
    slow = _SlowItems({"host/1": 11}, started)

    def finish() -> None:
        store.finish_populate(
            "2026-09-01", "id-a", members=slow, complete=True,
        )

    finish_thread = threading.Thread(target=finish)
    waiter_thread = threading.Thread(target=waiter)
    finish_thread.start()
    waiter_thread.start()
    finish_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert store.lookup_member("2026-09-01", "id-a", "host/1") == 11


@pytest.mark.django_db(databases=[])
def test_store_complete_lock_hold_normalizes_before_rlock(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)
    slow = _SlowItems({"host/1": 11}, started)

    def complete() -> None:
        store.store_complete("2026-09-01", "id-a", slow)

    complete_thread = threading.Thread(target=complete)
    waiter_thread = threading.Thread(target=waiter)
    complete_thread.start()
    waiter_thread.start()
    complete_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert store.lookup_complete_map("2026-09-01", "id-a") == {"host/1": 11}


@pytest.mark.django_db(databases=[])
def test_merge_members_lock_hold_cas_none_first_key(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert store.merge_members("2026-09-01", "id-a", {"host/1": 11}) is True
    assert store.lookup_complete_map("2026-09-01", "id-a") == {"host/1": 11}


@pytest.mark.django_db(databases=[])
def test_merge_members_lock_hold_concurrent_cas(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    errors = []

    def merge_one(name: str, size: int) -> None:
        try:
            store.merge_members("2026-09-01", "id-a", {name: size})
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=merge_one, args=("host/1", 11))
    t2 = threading.Thread(target=merge_one, args=("host/2", 22))
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)
    assert errors == []
    got = store.lookup_complete_map("2026-09-01", "id-a")
    assert got == {"host/1": 11, "host/2": 22}


@pytest.mark.django_db(databases=[])
def test_merge_members_lock_hold_copy_releases_lock(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    started = threading.Event()
    slow = _SlowMemberMap({"host/1": 11}, started, hold_s=0.5)
    with store._lock:
        store._members[("2026-09-01", "id-a")] = slow
        store._complete[("2026-09-01", "id-a")] = True
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    def merge() -> None:
        assert store.merge_members("2026-09-01", "id-a", {"host/2": 22}) is True

    merge_thread = threading.Thread(target=merge)
    waiter_thread = threading.Thread(target=waiter)
    merge_thread.start()
    waiter_thread.start()
    merge_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert store.lookup_complete_map("2026-09-01", "id-a") == {
        "host/1": 11, "host/2": 22,
    }


@pytest.mark.django_db(databases=[])
def test_members_load_hydrate_lock_hold_builds_outside_rlock(
    tmp_path, monkeypatch,
):
    archive = str(tmp_path / "archive")
    store = SyncTimedbArchiveMembersStore(archive)
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    def fake_isdir(_path: str) -> bool:
        return True

    def fake_listdir(_path: str) -> list[str]:
        return ["2026-09-01.json"]

    def fake_load(_path: str, _kind: str, default=None):
        return {
            "day_token": "2026-09-01",
            "identities": {
                "id-a": {
                    "complete": True,
                    "members": _SlowMemberMap({"host/1": 11}, started),
                },
            },
        }

    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_store.os.path.isdir",
        fake_isdir,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_store.os.listdir",
        fake_listdir,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_store.load_persistence_document",
        fake_load,
    )
    load_thread = threading.Thread(target=store.load)
    waiter_thread = threading.Thread(target=waiter)
    load_thread.start()
    waiter_thread.start()
    load_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert store.lookup_member("2026-09-01", "id-a", "host/1") == 11


@pytest.mark.django_db(databases=[])
def test_get_day_skip_lock_hold_copies_after_release(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    started = threading.Event()

    class SlowSkip(Mapping):
        def __init__(self, data):
            self._data = dict(data)

        def __iter__(self):
            started.set()
            time.sleep(0.4)
            return iter(self._data)

        def __getitem__(self, key):
            return self._data[key]

        def __len__(self):
            return len(self._data)

    slow = SlowSkip({"kind": "read_error", "detail": "x"})
    with store._lock:
        store._day_skip["2026-09-01"] = slow
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    def lookup() -> None:
        assert store.get_day_skip("2026-09-01") == {
            "kind": "read_error", "detail": "x",
        }

    lookup_thread = threading.Thread(target=lookup)
    waiter_thread = threading.Thread(target=waiter)
    lookup_thread.start()
    waiter_thread.start()
    lookup_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]


@pytest.mark.django_db(databases=[])
def test_event_set_lock_hold_after_release(tmp_path):
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert store.try_begin_populate("2026-09-01", "id-a")
    started = threading.Event()
    slow_event = _SlowEvent(started)
    with store._lock:
        store._events[("2026-09-01", "id-a")] = slow_event
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    def finish() -> None:
        store.finish_populate(
            "2026-09-01", "id-a", members={"host/1": 11}, complete=True,
        )

    finish_thread = threading.Thread(target=finish)
    waiter_thread = threading.Thread(target=waiter)
    finish_thread.start()
    waiter_thread.start()
    finish_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert slow_event.is_set()


@pytest.mark.django_db(databases=[])
def test_dequeue_populate_prefers_hot_over_cold_without_scan(tmp_path):
    """Hot deque popleft must beat cold without an O(n) rank scan."""
    store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
    assert store.enqueue_populate({"day_token": "2026-01-01", "kind": "cold"})
    store.set_ingest_tar_hot("2026-01-02", reason="chunk_prewarm")
    assert store.enqueue_populate({"day_token": "2026-01-02", "kind": "hot"})
    store.set_ingest_tar_hot("2026-01-03", reason="populate_wait")
    assert store.enqueue_populate({"day_token": "2026-01-03", "kind": "hot2"})
    first = store.dequeue_populate(timeout_s=0.05)
    second = store.dequeue_populate(timeout_s=0.05)
    third = store.dequeue_populate(timeout_s=0.05)
    assert first["day_token"] == "2026-01-02"
    assert second["day_token"] == "2026-01-03"
    assert third["day_token"] == "2026-01-01"
    assert store.dequeue_populate(timeout_s=0.01) is None
    assert store._populate_queue_empty_locked()
