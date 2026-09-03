"""In-process archive-members store: single-flight populate and sticky skip."""
from __future__ import annotations

import threading
import time

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
