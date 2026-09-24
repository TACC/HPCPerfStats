"""In-process sync_timedb job-store claim, band, and snapshot contracts."""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date

import pytest

from hpcperfstats.dbload.lib.sync_timedb_job_store import (
    CATCHUP_SCORE_BASE,
    JOB_KIND_APPEND,
    JOB_KIND_INGEST,
    SyncTimedbJobStore,
    ack_job,
    claim_ingest_job,
    claim_list_job,
    encode_ingest_score,
    enqueue_list_job,
    make_lease_owner_token,
    requeue_job,
    zadd_ingest_job,
)
import hpcperfstats.dbload.lib.sync_timedb_job_store as job_store_mod


@pytest.mark.django_db(databases=[])
def test_claim_ack_same_identity_cannot_double_claim(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/a", score=1.0)
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    first = claim_ingest_job(store, band="hot", owner_token=owner)
    assert first is not None
    assert first.identity == "/raw/a"
    second = claim_ingest_job(
        store,
        band="hot",
        owner_token=make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
    )
    assert second is None
    assert ack_job(
        store,
        kind=JOB_KIND_INGEST,
        identity="/raw/a",
        owner_token=owner,
    )
    assert claim_ingest_job(
        store,
        band="hot",
        owner_token=make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
    ) is None


@pytest.mark.django_db(databases=[])
def test_hot_and_catchup_bands_claim_independently(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    today = date(2026, 8, 24)
    hot_score = encode_ingest_score(
        band="hot",
        day=today,
        today=today,
        identity="/raw/hot",
    )
    catch_score = encode_ingest_score(
        band="catchup",
        day=date(2026, 1, 1),
        today=today,
        identity="/raw/catch",
    )
    assert hot_score < CATCHUP_SCORE_BASE
    assert catch_score >= CATCHUP_SCORE_BASE
    zadd_ingest_job(store, identity="/raw/hot", score=hot_score)
    zadd_ingest_job(store, identity="/raw/catch", score=catch_score)
    hot = claim_ingest_job(
        store,
        band="hot",
        owner_token=make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
    )
    catch = claim_ingest_job(
        store,
        band="catchup",
        owner_token=make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
    )
    assert hot is not None and hot.identity == "/raw/hot"
    assert catch is not None and catch.identity == "/raw/catch"


@pytest.mark.django_db(databases=[])
def test_list_dedupe_skips_queued_and_inflight(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    first = enqueue_list_job(
        store, kind=JOB_KIND_APPEND, identity="/tar/a", dedupe=True,
    )
    second = enqueue_list_job(
        store, kind=JOB_KIND_APPEND, identity="/tar/a", dedupe=True,
    )
    assert first > 0
    assert second == 0
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claim = claim_list_job(store, kind=JOB_KIND_APPEND, owner_token=owner)
    assert claim is not None
    third = enqueue_list_job(
        store, kind=JOB_KIND_APPEND, identity="/tar/a", dedupe=True,
    )
    assert third == 0


@pytest.mark.django_db(databases=[])
def test_requeue_restores_identity_and_owner_check(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/r", score=2.0)
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claim = claim_ingest_job(store, band="hot", owner_token=owner)
    assert claim is not None
    assert not requeue_job(
        store,
        kind=JOB_KIND_INGEST,
        identity="/raw/r",
        owner_token="wrong:h:b:1",
        score=2.0,
    )
    assert requeue_job(
        store,
        kind=JOB_KIND_INGEST,
        identity="/raw/r",
        owner_token=owner,
        score=2.0,
    )
    again = claim_ingest_job(
        store,
        band="hot",
        owner_token=make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
    )
    assert again is not None
    assert again.identity == "/raw/r"


@pytest.mark.django_db(databases=[])
def test_reload_keeps_queues_drops_inflight(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbJobStore(archive)
    zadd_ingest_job(store, identity="/raw/queued", score=3.0)
    zadd_ingest_job(store, identity="/raw/busy", score=4.0)
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claim = claim_ingest_job(store, band="hot", owner_token=owner)
    assert claim is not None
    store.persist(force=True)
    revived = SyncTimedbJobStore(archive)
    queued = set(revived.ingest_identities())
    assert "/raw/busy" in queued
    assert "/raw/queued" not in queued
    assert revived.inflight_count(JOB_KIND_INGEST) == 0


@pytest.mark.django_db(databases=[])
def test_persist_releases_lock_before_disk_write(tmp_path, monkeypatch):
    """persist must not hold the job-store RLock across save_persistence_document."""
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/a", score=1.0)
    started = threading.Event()
    real_save = job_store_mod.save_persistence_document
    acquired = []

    def slow_save(*args, **kwargs):
        started.set()
        time.sleep(0.5)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(job_store_mod, "save_persistence_document", slow_save)

    def persist() -> None:
        store.persist(force=True)

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


@pytest.mark.django_db(databases=[])
def test_empty_snapshot_is_valid_hint_not_caught_up(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    store.persist(force=True)
    revived = SyncTimedbJobStore(str(tmp_path / "archive"))
    assert revived.ingest_identities() == []
    assert revived.snapshot_is_empty() is True


@pytest.mark.django_db(databases=[])
def test_reload_drops_payload_for_claimed_and_orphan_identities(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbJobStore(archive)
    zadd_ingest_job(store, identity="/raw/keep", score=5.0, fingerprint="keep-fp")
    zadd_ingest_job(store, identity="/raw/claim", score=1.0, fingerprint="claim-fp")
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claim = claim_ingest_job(store, band="hot", owner_token=owner)
    assert claim is not None
    assert claim.identity == "/raw/claim"
    store._payloads[(JOB_KIND_INGEST, "/raw/orphan")] = {
        "fingerprint": "dead",
        "attempt": "9",
    }
    store.persist(force=True)
    assert (JOB_KIND_INGEST, "/raw/claim") in store._payloads
    assert (JOB_KIND_INGEST, "/raw/orphan") not in store._payloads
    revived = SyncTimedbJobStore(archive)
    assert "/raw/keep" in revived.ingest_identities()
    assert "/raw/claim" not in revived.ingest_identities()
    assert (JOB_KIND_INGEST, "/raw/keep") in revived._payloads
    assert (JOB_KIND_INGEST, "/raw/claim") not in revived._payloads
    assert (JOB_KIND_INGEST, "/raw/orphan") not in revived._payloads


@pytest.mark.django_db(databases=[])
def test_ack_drops_inflight_lease_and_payload(tmp_path):
    """ACK must drop heap occupancy so a finished job cannot leak leases."""
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/done", score=1.0, fingerprint="fp")
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claim = claim_ingest_job(store, band="hot", owner_token=owner)
    assert claim is not None
    assert store.inflight_count(JOB_KIND_INGEST) == 1
    assert (JOB_KIND_INGEST, "/raw/done") in store._payloads
    ack_job(store, kind=JOB_KIND_INGEST, identity="/raw/done", owner_token=owner)
    assert store.inflight_count(JOB_KIND_INGEST) == 0
    assert store.lease_token("ingest", "/raw/done") is None
    assert (JOB_KIND_INGEST, "/raw/done") not in store._payloads


def _lock_hold_waiter(lock, started, timeout=0.05):
    acquired = []

    def waiter() -> None:
        assert started.wait(timeout=2)
        got = lock.acquire(timeout=timeout)
        acquired.append(got)
        if got:
            lock.release()

    return acquired, waiter


@pytest.mark.django_db(databases=[])
def test_claim_ingest_lock_hold_no_double_claim(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    for idx in range(20):
        zadd_ingest_job(store, identity="/raw/%s" % idx, score=1.0)
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claimed: list[str] = []

    def worker() -> None:
        jobs = store.claim_ingest(band="hot", owner_token=owner, max_n=20)
        claimed.extend(job.identity for job in jobs)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


@pytest.mark.django_db(databases=[])
def test_claim_ingest_lock_hold_live_score_skip(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/a", score=1.0)
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    real_heappop = job_store_mod.heapq.heappop

    def reband_heappop(heap):
        with store._lock:
            store._ingest["/raw/a"] = float(CATCHUP_SCORE_BASE)
        return real_heappop(heap)

    monkeypatch.setattr(job_store_mod.heapq, "heappop", reband_heappop)
    claimed = store.claim_ingest(band="hot", owner_token=owner, max_n=1)
    assert claimed == []
    assert store.ingest_score("/raw/a") == float(CATCHUP_SCORE_BASE)
    catch = store.claim_ingest(
        band="catchup",
        owner_token=owner,
        max_n=1,
    )
    assert [job.identity for job in catch] == ["/raw/a"]


@pytest.mark.django_db(databases=[])
def test_claim_ingest_heap_order_and_band_separation(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/hot-hi", score=5.0)
    zadd_ingest_job(store, identity="/raw/hot-lo", score=1.0)
    zadd_ingest_job(
        store,
        identity="/raw/catch",
        score=float(CATCHUP_SCORE_BASE) + 1.0,
    )
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    hot = store.claim_ingest(band="hot", owner_token=owner, max_n=2)
    assert [job.identity for job in hot] == ["/raw/hot-lo", "/raw/hot-hi"]
    catch = store.claim_ingest(band="catchup", owner_token=owner, max_n=1)
    assert [job.identity for job in catch] == ["/raw/catch"]


@pytest.mark.django_db(databases=[])
def test_claim_ingest_heap_rebuilds_on_load(tmp_path):
    archive = str(tmp_path / "archive")
    store = SyncTimedbJobStore(archive)
    zadd_ingest_job(store, identity="/raw/b", score=2.0)
    zadd_ingest_job(store, identity="/raw/a", score=1.0)
    store.persist(force=True)
    reloaded = SyncTimedbJobStore(archive)
    owner = make_lease_owner_token(pid=1, hostname="h", boot_id="b")
    claimed = reloaded.claim_ingest(band="hot", owner_token=owner, max_n=2)
    assert [job.identity for job in claimed] == ["/raw/a", "/raw/b"]


@pytest.mark.django_db(databases=[])
def test_persist_lock_hold_save_fail_leaves_dirty(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/a", score=1.0)

    def boom(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(job_store_mod, "save_persistence_document", boom)
    with pytest.raises(OSError):
        store.persist(force=True)
    assert store._dirty is True


@pytest.mark.django_db(databases=[])
def test_persist_lock_hold_wrap_releases_lock(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    store.enqueue_list(JOB_KIND_APPEND, "/tar/a")
    store.enqueue_list(JOB_KIND_APPEND, "/tar/b")
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)
    real_sorted = sorted

    def slow_sorted(*args, **kwargs):
        started.set()
        time.sleep(0.4)
        return real_sorted(*args, **kwargs)

    monkeypatch.setattr("builtins.sorted", slow_sorted)
    persist_thread = threading.Thread(target=lambda: store.persist(force=True))
    waiter_thread = threading.Thread(target=waiter)
    persist_thread.start()
    waiter_thread.start()
    persist_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]


@pytest.mark.django_db(databases=[])
def test_ingest_identities_lock_hold_sort_releases_lock(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    zadd_ingest_job(store, identity="/raw/b", score=2.0)
    zadd_ingest_job(store, identity="/raw/a", score=1.0)
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)
    real_sorted = sorted

    def slow_sorted(*args, **kwargs):
        started.set()
        time.sleep(0.4)
        return real_sorted(*args, **kwargs)

    monkeypatch.setattr("builtins.sorted", slow_sorted)
    result = []

    def run() -> None:
        result.extend(store.ingest_identities())

    run_thread = threading.Thread(target=run)
    waiter_thread = threading.Thread(target=waiter)
    run_thread.start()
    waiter_thread.start()
    run_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert result == ["/raw/a", "/raw/b"]


@pytest.mark.django_db(databases=[])
def test_score_range_lock_hold_count_releases_lock(tmp_path):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    started = threading.Event()

    class SlowScore(float):
        def __new__(cls, value, started):
            obj = float.__new__(cls, value)
            obj._started = started
            return obj

        def __le__(self, other):
            self._started.set()
            time.sleep(0.05)
            return float.__le__(self, other)

    with store._lock:
        store._ingest["/raw/a"] = SlowScore(1.0, started)
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    def run() -> None:
        assert store.ingest_count_in_score_range(0, 2) == 1

    run_thread = threading.Thread(target=run)
    waiter_thread = threading.Thread(target=waiter)
    run_thread.start()
    waiter_thread.start()
    run_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]


@pytest.mark.django_db(databases=[])
def test_reorder_list_lock_hold_cas_vs_popleft(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    store.enqueue_list(JOB_KIND_APPEND, "a")
    store.enqueue_list(JOB_KIND_APPEND, "b")
    real_list = list
    popped = {"n": 0}

    def spy_list(obj=(), *args, **kwargs):
        out = real_list(obj, *args, **kwargs)
        if popped["n"] == 0 and out == ["a", "b"]:
            popped["n"] += 1
            store._lists[JOB_KIND_APPEND].popleft()
        return out

    monkeypatch.setattr("builtins.list", spy_list)
    assert store.reorder_list(JOB_KIND_APPEND, ["b", "a"]) is False
    assert list(store._lists[JOB_KIND_APPEND]) == ["b"]


@pytest.mark.django_db(databases=[])
def test_capacity_limit_lock_hold_getter_outside_rlock(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    def slow_cap() -> int:
        started.set()
        time.sleep(0.4)
        return 2_000_000

    monkeypatch.setattr(job_store_mod, "queue_capacity_limit", slow_cap)
    zadd_thread = threading.Thread(
        target=lambda: zadd_ingest_job(store, identity="/raw/a", score=1.0),
    )
    waiter_thread = threading.Thread(target=waiter)
    zadd_thread.start()
    waiter_thread.start()
    zadd_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]


@pytest.mark.django_db(databases=[])
def test_job_load_lock_hold_builds_outside_rlock(tmp_path, monkeypatch):
    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    started = threading.Event()
    acquired, waiter = _lock_hold_waiter(store._lock, started)

    class SlowIngest(dict):
        def items(self):
            started.set()
            time.sleep(0.4)
            return list(super().items())

    def fake_load(*_args, **_kwargs):
        return {
            "ingest": SlowIngest({"/raw/a": 1.0}),
            "lists": {},
            "pending": {},
            "payloads": {},
        }

    monkeypatch.setattr(job_store_mod, "load_persistence_document", fake_load)
    load_thread = threading.Thread(target=store.load)
    waiter_thread = threading.Thread(target=waiter)
    load_thread.start()
    waiter_thread.start()
    load_thread.join(timeout=3)
    waiter_thread.join(timeout=3)
    assert acquired == [True]
    assert store.ingest_identities() == ["/raw/a"]


@pytest.mark.django_db(databases=[])
def test_list_slice_lock_hold_does_not_copy_whole_deque(tmp_path):
    class CountingDeque(deque):
        visits = 0

        def __iter__(self):
            for item in deque.__iter__(self):
                type(self).visits += 1
                yield item

    store = SyncTimedbJobStore(str(tmp_path / "archive"))
    CountingDeque.visits = 0
    store._lists[JOB_KIND_APPEND] = CountingDeque(["head"] + ["tail"] * 99)
    got = store.list_slice(JOB_KIND_APPEND, 0, 0)
    assert got == ["head"]
    assert CountingDeque.visits == 1
