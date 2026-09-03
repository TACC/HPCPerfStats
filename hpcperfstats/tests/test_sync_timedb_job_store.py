"""In-process sync_timedb job-store claim, band, and snapshot contracts."""
from __future__ import annotations

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
