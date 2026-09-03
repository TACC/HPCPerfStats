"""Regression tests for job-store claim/ack/requeue/reap crash safety."""
from __future__ import annotations

import json
import os

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_store as jq
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore

OWNER_A = "nonceA:host1:boot1:1001"
OWNER_B = "nonceB:host1:boot1:1002"


@pytest.fixture(autouse=True)
def _reset_scripts():
  jq.reset_job_queue_script_cache_for_tests()
  yield
  jq.reset_job_queue_script_cache_for_tests()


def _store() -> SyncTimedbJobStore:
  return SyncTimedbJobStore("")


def _seed_ingest(store, identity, score):
  jq.zadd_ingest_job(store, identity=identity, score=score)


def test_claim_ingest_moves_member_to_inflight_under_lease():
  """A claim must leave no window where work is neither queued nor in flight."""
  store = _store()
  _seed_ingest(store, "p|1|1", 5)

  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  assert claim is not None
  assert claim.identity == "p|1|1"
  assert claim.score == 5.0
  assert claim.deadline == pytest.approx(1060.0)
  assert store.zcard(jq.job_queue_key("ingest")) == 0
  inflight = jq.read_inflight_entries(store, kind="ingest")
  assert set(inflight) == {"p|1|1"}
  assert inflight["p|1|1"][1] == OWNER_A
  assert store.get(jq.job_lease_key("ingest", "p|1|1")) == OWNER_A


def test_claim_skips_leased_member_and_deprioritizes_it():
  """An in-flight lease must not be claimed by a second owner."""
  store = _store()
  _seed_ingest(store, "held|1|1", 5)
  _seed_ingest(store, "free|1|1", 9)
  store._leases[(jq.JOB_KIND_INGEST, "held|1|1")] = OWNER_B
  store._inflight[jq.JOB_KIND_INGEST]["held|1|1"] = (2000.0, OWNER_B, 5.0)
  store._ingest.pop("held|1|1", None)

  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  assert claim is not None and claim.identity == "free|1|1"
  inflight = jq.read_inflight_entries(store, kind="ingest")
  assert inflight["held|1|1"][1] == OWNER_B


def test_crashed_owner_ingest_job_is_reaped_back_onto_queue():
  """A worker that dies mid-job must not silently drop the file forever."""
  store = _store()
  _seed_ingest(store, "p|1|1", 7)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None

  too_early = jq.reap_expired_inflight(
      store, kind="ingest", now_s=1061.0, ttl_s=60,
  )
  assert too_early == []

  reaped = jq.reap_expired_inflight(
      store, kind="ingest", now_s=1200.0, ttl_s=60,
  )

  assert reaped == ["p|1|1"]
  assert jq.read_inflight_entries(store, kind="ingest") == {}
  assert store.get(jq.job_lease_key("ingest", "p|1|1")) is None
  assert store.zscore(jq.job_queue_key("ingest"), "p|1|1") == (
      7.0 + jq.LEASE_CONFLICT_SCORE_PENALTY
  )


def test_reaped_job_reclaim_survives_late_ack_from_dead_owner():
  """A late ack from a reaped owner must not erase the new owner's claim."""
  store = _store()
  _seed_ingest(store, "p|1|1", 7)
  first = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert first is not None
  jq.reap_expired_inflight(store, kind="ingest", now_s=1200.0, ttl_s=60)
  second = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_B, ttl_s=60, now_s=1200.0,
  )
  assert second is not None and second.owner_token == OWNER_B
  jq.bump_job_attempt(store, kind="ingest", identity="p|1|1")
  jq.write_job_fingerprint(
      store, kind="ingest", identity="p|1|1", fingerprint="9|9",
  )

  assert not jq.ack_job(
      store, kind="ingest", identity="p|1|1", owner_token=OWNER_A,
  )

  inflight = jq.read_inflight_entries(store, kind="ingest")
  assert inflight["p|1|1"][1] == OWNER_B
  assert store.get(jq.job_lease_key("ingest", "p|1|1")) == OWNER_B
  assert jq.read_job_attempt(store, kind="ingest", identity="p|1|1") == 1
  assert jq.read_job_fingerprint(
      store, kind="ingest", identity="p|1|1",
  ) == "9|9"


def test_late_ack_non_owner_preserves_list_pending_and_payload():
  """A non-owner ACK must not drop the new owner's LIST claim."""
  store = _store()
  jq.enqueue_list_job(store, kind="append", identity="/raw/a", dedupe=True)
  first = jq.claim_list_job(
      store, kind="append", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert first is not None
  jq.reap_expired_inflight(store, kind="append", now_s=1200.0, ttl_s=60)
  second = jq.claim_list_job(
      store, kind="append", owner_token=OWNER_B, ttl_s=60, now_s=1200.0,
  )
  assert second is not None
  jq.bump_job_attempt(store, kind="append", identity="/raw/a")

  assert not jq.ack_job(
      store, kind="append", identity="/raw/a", owner_token=OWNER_A,
  )

  assert jq.read_job_attempt(store, kind="append", identity="/raw/a") == 1
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 0


def test_ack_clears_inflight_lease_payload_and_pending():
  store = _store()
  jq.enqueue_list_job(store, kind="append", identity="/raw/a", dedupe=True)
  claim = jq.claim_list_job(
      store, kind="append", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  jq.bump_job_attempt(store, kind="append", identity="/raw/a")

  assert jq.ack_job(
      store, kind="append", identity="/raw/a", owner_token=OWNER_A,
  )

  assert jq.read_inflight_entries(store, kind="append") == {}
  assert store.get(jq.job_lease_key("append", "/raw/a")) is None
  assert jq.read_job_attempt(store, kind="append", identity="/raw/a") == 0
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 1


def test_requeue_returns_ingest_job_with_explicit_score():
  store = _store()
  _seed_ingest(store, "p|1|1", 7)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None

  assert jq.requeue_job(
      store,
      kind="ingest",
      identity="p|1|1",
      owner_token=OWNER_A,
      score=claim.score,
  )

  assert store.zscore(jq.job_queue_key("ingest"), "p|1|1") == 7.0
  assert jq.read_inflight_entries(store, kind="ingest") == {}
  assert store.get(jq.job_lease_key("ingest", "p|1|1")) is None


def test_requeue_without_score_lands_in_catchup_band():
  store = _store()
  _seed_ingest(store, "p|1|1", 7)
  jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  jq.requeue_job(
      store, kind="ingest", identity="p|1|1", owner_token=OWNER_A,
  )

  score = store.zscore(jq.job_queue_key("ingest"), "p|1|1")
  assert jq.decode_ingest_band(score) == "catchup"


def test_renew_extends_deadline_so_reaper_leaves_live_owner_alone():
  """Long jobs stay owned only while they keep renewing."""
  store = _store()
  _seed_ingest(store, "p|1|1", 7)
  jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  assert jq.renew_job_lease(
      store,
      kind="ingest",
      identity="p|1|1",
      owner_token=OWNER_A,
      ttl_s=60,
      now_s=1150.0,
  )

  assert jq.reap_expired_inflight(
      store, kind="ingest", now_s=1200.0, ttl_s=60,
  ) == []
  assert jq.read_inflight_entries(store, kind="ingest")["p|1|1"][0] == (
      pytest.approx(1210.0)
  )


def test_renew_fails_for_non_owner():
  store = _store()
  _seed_ingest(store, "p|1|1", 7)
  jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert not jq.renew_job_lease(
      store, kind="ingest", identity="p|1|1", owner_token=OWNER_B, ttl_s=60,
  )


def test_lease_ttl_matches_oq1_and_grace_is_small(monkeypatch):
  """OQ-1: lease TTL follows per-file max; reap grace stays floor-sized."""
  monkeypatch.setattr(
      jq.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 86400,
  )
  assert jq.job_lease_ttl_seconds() == 86400
  assert jq.INFLIGHT_REAP_GRACE_FLOOR_S == 30
  monkeypatch.setattr(
      jq.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 10,
  )
  assert jq.job_lease_ttl_seconds() == jq.JOB_LEASE_TTL_FLOOR_S


def test_steal_dead_owner_requeues_inflight():
  """Steal must clear inflight and restore the queued member immediately."""
  store = _store()
  _seed_ingest(store, "/raw/steal", 7)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert store.zcard(jq.job_queue_key("ingest")) == 0

  assert jq.steal_job_lease_if_owner_dead(
      store,
      kind="ingest",
      identity="/raw/steal",
      pid_alive_fn=lambda _p: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("ingest", "/raw/steal")) is None
  assert jq.read_inflight_entries(store, kind="ingest") == {}
  assert store.zscore(jq.job_queue_key("ingest"), "/raw/steal") is not None


def test_hot_range_claims_negative_scores():
  """Clock skew can mint a negative hot score; it must still be claimable."""
  store = _store()
  _seed_ingest(store, "skewed|1|1", -5)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None and claim.identity == "skewed|1|1"


def test_steal_lease_requires_local_host_and_boot():
  """PID probes are meaningless across hosts and across reboots."""
  store = _store()
  store._leases[(jq.JOB_KIND_APPEND, "day")] = "n:otherhost:boot1:99999"
  assert not jq.steal_job_lease_if_owner_dead(
      store,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("append", "day")) == "n:otherhost:boot1:99999"

  store._leases[(jq.JOB_KIND_APPEND, "day")] = "n:host1:oldboot:99999"
  assert not jq.steal_job_lease_if_owner_dead(
      store,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )

  store._leases[(jq.JOB_KIND_APPEND, "day")] = "n:host1:boot1:99999"
  assert jq.steal_job_lease_if_owner_dead(
      store,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("append", "day")) is None
  assert store.llen(jq.job_queue_key("append")) == 0


def test_steal_list_without_inflight_does_not_rpush():
  """LIST steal with a lease-only (no inflight) entry must not fabricate a job."""
  store = _store()
  identity = "/raw/ghost-append"
  store._leases[(jq.JOB_KIND_APPEND, identity)] = "n:host1:boot1:99999"
  assert store.llen(jq.job_queue_key("append")) == 0
  assert jq.steal_job_lease_if_owner_dead(
      store,
      kind="append",
      identity=identity,
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("append", identity)) is None
  assert store.llen(jq.job_queue_key("append")) == 0


def test_steal_lease_skips_legacy_two_field_token():
  store = _store()
  store._leases[(jq.JOB_KIND_APPEND, "day")] = "legacy:99999"
  assert not jq.steal_job_lease_if_owner_dead(
      store,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("append", "day")) == "legacy:99999"


def test_owner_token_embeds_host_and_boot():
  token = jq.make_lease_owner_token(pid=7, hostname="h:1", boot_id="b|2")
  owner = jq.parse_lease_owner(token)
  assert owner.hostname == "h_1"
  assert owner.boot_id == "b_2"
  assert owner.pid == 7


def test_dedupe_enqueue_skips_queued_and_inflight_identities():
  """Repeated discover passes must not grow the append queue without bound."""
  store = _store()
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 1
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 0
  assert store.llen(jq.job_queue_key("append")) == 1

  claim = jq.claim_list_job(
      store, kind="append", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 0
  assert store.llen(jq.job_queue_key("append")) == 0


def test_list_claim_conflict_puts_member_back_on_queue():
  store = _store()
  jq.enqueue_list_job(store, kind="day_close", identity="/d/2026-08-01.tar")
  store._leases[(jq.JOB_KIND_DAY_CLOSE, "/d/2026-08-01.tar")] = OWNER_B
  store._inflight[jq.JOB_KIND_DAY_CLOSE]["/d/2026-08-01.tar"] = (
      2000.0, OWNER_B, None,
  )
  store._lists[jq.JOB_KIND_DAY_CLOSE].clear()
  store._pending[jq.JOB_KIND_DAY_CLOSE].discard("/d/2026-08-01.tar")
  assert jq.claim_list_job(
      store, kind="day_close", owner_token=OWNER_A, ttl_s=60,
  ) is None
  inflight = jq.read_inflight_entries(store, kind="day_close")
  assert inflight["/d/2026-08-01.tar"][1] == OWNER_B


def test_queue_capacity_limit_blocks_unbounded_growth():
  store = _store()
  for index in range(3):
    _seed_ingest(store, "p|%d|1" % index, index)
  assert jq.queue_has_capacity(store, kind="ingest", limit=4)
  assert not jq.queue_has_capacity(store, kind="ingest", limit=3)


def test_queue_census_counts_queued_and_inflight():
  store = _store()
  _seed_ingest(store, "p|1|1", 1)
  _seed_ingest(store, "p|2|1", 2)
  jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  jq.enqueue_list_job(store, kind="append", identity="/raw/a")
  census = jq.queue_census(store)
  assert census["ingest"] == {"queued": 1, "inflight": 1}
  assert census["append"] == {"queued": 1, "inflight": 0}
  assert "ingest=1/1" in jq.format_queue_census(census)


def test_count_inflight_by_band_uses_recorded_scores():
  store = _store()
  _seed_ingest(store, "hot|1|1", 5)
  _seed_ingest(store, "cold|1|1", jq.CATCHUP_SCORE_BASE + 5)
  jq.claim_ingest_job(
      store, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  jq.claim_ingest_job(
      store, band="catchup", owner_token=OWNER_B, ttl_s=60, now_s=1000.0,
  )
  assert jq.count_inflight_by_band(store) == (1, 1)


def test_attempt_counter_increments_and_dead_letter_persists(tmp_path):
  store = _store()
  assert jq.bump_job_attempt(store, kind="ingest", identity="p|1|1") == 1
  assert jq.bump_job_attempt(store, kind="ingest", identity="p|1|1") == 2
  assert jq.read_job_attempt(store, kind="ingest", identity="p|1|1") == 2

  archive_dir = str(tmp_path)
  assert jq.append_queue_dead_letter(
      archive_dir,
      kind="ingest",
      identity="p|1|1",
      attempt=2,
      reason="parse failure",
  )
  path = jq.queue_dead_letter_path(archive_dir)
  assert os.path.isfile(path)
  with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert payload["entries"][0]["identity"] == "p|1|1"
  assert payload["entries"][0]["reason"] == "parse failure"


def test_dead_letter_kind_is_separate_from_archive_dead_letter():
  from hpcperfstats.dbload.lib import sync_timedb_persistence as persist

  assert (
      persist.PERSISTENCE_ARTIFACT_REGISTRY["queue_dead_letter"]
      != persist.PERSISTENCE_ARTIFACT_REGISTRY["archive_dead_letter"]
  )


def test_reap_recovers_list_kind_without_score():
  store = _store()
  jq.enqueue_list_job(store, kind="day_close", identity="/d/x.tar")
  jq.claim_list_job(
      store, kind="day_close", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  reaped = jq.reap_expired_inflight(
      store, kind="day_close", now_s=1200.0, ttl_s=60,
  )
  assert reaped == ["/d/x.tar"]
  assert store.lrange(jq.job_queue_key("day_close"), 0, -1) == ["/d/x.tar"]


def test_reap_respects_limit():
  store = _store()
  for index in range(5):
    _seed_ingest(store, "p|%d|1" % index, index)
    jq.claim_ingest_job(
        store, band="hot", owner_token="n:h:b:%d" % index, ttl_s=60,
        now_s=1000.0,
    )
  reaped = jq.reap_expired_inflight(
      store, kind="ingest", now_s=1200.0, limit=2, ttl_s=60,
  )
  assert len(reaped) == 2
  assert store.hlen(jq.job_inflight_key("ingest")) == 3
