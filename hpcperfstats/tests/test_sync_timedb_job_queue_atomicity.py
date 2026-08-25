"""Regression tests for job:v1 claim/ack/requeue/reap crash safety."""
from __future__ import annotations

import json
import os

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.tests.fake_redis_queue import FakeRedis

OWNER_A = "nonceA:host1:boot1:1001"
OWNER_B = "nonceB:host1:boot1:1002"


@pytest.fixture(autouse=True)
def _reset_scripts():
  jq.reset_job_queue_script_cache_for_tests()
  yield
  jq.reset_job_queue_script_cache_for_tests()


def _seed_ingest(client, identity, score):
  jq.zadd_ingest_job(client, identity=identity, score=score)


def test_claim_ingest_moves_member_to_inflight_under_lease():
  """A claim must leave no window where work is neither queued nor in flight."""
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 5)

  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  assert claim is not None
  assert claim.identity == "p|1|1"
  assert claim.score == 5.0
  assert claim.deadline == pytest.approx(1060.0)
  assert client.zcard(jq.job_queue_key("ingest")) == 0
  inflight = jq.read_inflight_entries(client, kind="ingest")
  assert set(inflight) == {"p|1|1"}
  assert inflight["p|1|1"][1] == OWNER_A
  assert client.get(jq.job_lease_key("ingest", "p|1|1")) == OWNER_A


def test_claim_skips_leased_member_and_deprioritizes_it():
  """A lease conflict must not pop-and-drop the contended identity."""
  client = FakeRedis()
  _seed_ingest(client, "held|1|1", 5)
  _seed_ingest(client, "free|1|1", 9)
  client.set(jq.job_lease_key("ingest", "held|1|1"), OWNER_B, nx=True, ex=60)

  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  assert claim is not None and claim.identity == "free|1|1"
  held_score = client.zscore(jq.job_queue_key("ingest"), "held|1|1")
  assert held_score == 5.0 + jq.LEASE_CONFLICT_SCORE_PENALTY


def test_crashed_owner_ingest_job_is_reaped_back_onto_queue():
  """A worker that dies mid-job must not silently drop the file forever."""
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 7)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None

  too_early = jq.reap_expired_inflight(
      client, kind="ingest", now_s=1061.0, ttl_s=60,
  )
  assert too_early == []

  reaped = jq.reap_expired_inflight(
      client, kind="ingest", now_s=1200.0, ttl_s=60,
  )

  assert reaped == ["p|1|1"]
  assert jq.read_inflight_entries(client, kind="ingest") == {}
  assert client.get(jq.job_lease_key("ingest", "p|1|1")) is None
  assert client.zscore(jq.job_queue_key("ingest"), "p|1|1") == (
      7.0 + jq.LEASE_CONFLICT_SCORE_PENALTY
  )


def test_reaped_job_reclaim_survives_late_ack_from_dead_owner():
  """A late ack from a reaped owner must not erase the new owner's claim."""
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 7)
  first = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert first is not None
  jq.reap_expired_inflight(client, kind="ingest", now_s=1200.0, ttl_s=60)
  second = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_B, ttl_s=60, now_s=1200.0,
  )
  assert second is not None and second.owner_token == OWNER_B

  assert not jq.ack_job(
      client, kind="ingest", identity="p|1|1", owner_token=OWNER_A,
  )

  inflight = jq.read_inflight_entries(client, kind="ingest")
  assert inflight["p|1|1"][1] == OWNER_B
  assert client.get(jq.job_lease_key("ingest", "p|1|1")) == OWNER_B


def test_ack_clears_inflight_lease_payload_and_pending():
  client = FakeRedis()
  jq.enqueue_list_job(client, kind="append", identity="/raw/a", dedupe=True)
  claim = jq.claim_list_job(
      client, kind="append", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  jq.bump_job_attempt(client, kind="append", identity="/raw/a")

  assert jq.ack_job(
      client, kind="append", identity="/raw/a", owner_token=OWNER_A,
  )

  assert jq.read_inflight_entries(client, kind="append") == {}
  assert client.get(jq.job_lease_key("append", "/raw/a")) is None
  assert jq.read_job_attempt(client, kind="append", identity="/raw/a") == 0
  assert not client.sismember(jq.job_pending_set_key("append"), "/raw/a")


def test_requeue_returns_ingest_job_with_explicit_score():
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 7)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None

  assert jq.requeue_job(
      client,
      kind="ingest",
      identity="p|1|1",
      owner_token=OWNER_A,
      score=claim.score,
  )

  assert client.zscore(jq.job_queue_key("ingest"), "p|1|1") == 7.0
  assert jq.read_inflight_entries(client, kind="ingest") == {}
  assert client.get(jq.job_lease_key("ingest", "p|1|1")) is None


def test_requeue_without_score_lands_in_catchup_band():
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 7)
  jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  jq.requeue_job(
      client, kind="ingest", identity="p|1|1", owner_token=OWNER_A,
  )

  score = client.zscore(jq.job_queue_key("ingest"), "p|1|1")
  assert jq.decode_ingest_band(score) == "catchup"


def test_renew_extends_deadline_so_reaper_leaves_live_owner_alone():
  """Long jobs stay owned only while they keep renewing."""
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 7)
  jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )

  assert jq.renew_job_lease(
      client,
      kind="ingest",
      identity="p|1|1",
      owner_token=OWNER_A,
      ttl_s=60,
      now_s=1150.0,
  )

  assert jq.reap_expired_inflight(
      client, kind="ingest", now_s=1200.0, ttl_s=60,
  ) == []
  assert jq.read_inflight_entries(client, kind="ingest")["p|1|1"][0] == (
      pytest.approx(1210.0)
  )


def test_renew_fails_for_non_owner():
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 7)
  jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert not jq.renew_job_lease(
      client, kind="ingest", identity="p|1|1", owner_token=OWNER_B, ttl_s=60,
  )


def test_lease_ttl_is_short_and_bounded(monkeypatch):
  """A day-long lease would strand a crashed worker's file for a day."""
  monkeypatch.setattr(jq.cfg, "get_sync_pool_poll_timeout_s", lambda: 5)
  assert jq.job_lease_ttl_seconds() == jq.JOB_LEASE_TTL_FLOOR_S
  monkeypatch.setattr(jq.cfg, "get_sync_pool_poll_timeout_s", lambda: 600)
  assert jq.job_lease_ttl_seconds() == jq.JOB_LEASE_TTL_CEILING_S
  monkeypatch.setattr(
      jq.cfg, "get_sync_pool_poll_timeout_s", lambda: 0,
  )
  assert jq.job_lease_ttl_seconds() == jq.JOB_LEASE_TTL_FLOOR_S


def test_hot_range_claims_negative_scores():
  """Clock skew can mint a negative hot score; it must still be claimable."""
  client = FakeRedis()
  _seed_ingest(client, "skewed|1|1", -5)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None and claim.identity == "skewed|1|1"


def test_steal_lease_requires_local_host_and_boot():
  """PID probes are meaningless across hosts and across reboots."""
  client = FakeRedis()
  key = jq.job_lease_key("append", "day")
  client.set(key, "n:otherhost:boot1:99999", nx=True, ex=60)
  assert not jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert client.get(key) == "n:otherhost:boot1:99999"

  client.delete(key)
  client.set(key, "n:host1:oldboot:99999", nx=True, ex=60)
  assert not jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )

  client.delete(key)
  client.set(key, "n:host1:boot1:99999", nx=True, ex=60)
  assert jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert client.get(key) is None


def test_steal_lease_skips_legacy_two_field_token():
  client = FakeRedis()
  key = jq.job_lease_key("append", "day")
  client.set(key, "legacy:99999", nx=True, ex=60)
  assert not jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert client.get(key) == "legacy:99999"


def test_owner_token_embeds_host_and_boot():
  token = jq.make_lease_owner_token(pid=7, hostname="h:1", boot_id="b|2")
  owner = jq.parse_lease_owner(token)
  assert owner.hostname == "h_1"
  assert owner.boot_id == "b_2"
  assert owner.pid == 7


def test_eval_job_script_reloads_once_on_noscript():
  class _FlushingRedis(FakeRedis):
    def __init__(self):
      super().__init__()
      self.loads = 0

    def script_load(self, script):
      self.loads += 1
      sha = super().script_load(script)
      if self.loads == 1:
        with self._lock:
          self._scripts.clear()
      return sha

  client = _FlushingRedis()
  out = jq.eval_job_script(client, "-- HPS_REAP\n", 2, "i", "w", "0", "1",
                           "z", "L:", "1")
  assert out == []
  assert client.loads == 2


def test_eval_job_script_propagates_non_noscript_errors():
  """A connection error must not read as an empty queue."""

  class _BrokenRedis(FakeRedis):
    def evalsha(self, sha, numkeys, *args):
      raise RuntimeError("Connection reset by peer")

  client = _BrokenRedis()
  with pytest.raises(RuntimeError):
    jq.claim_ingest_job(client, band="hot", owner_token=OWNER_A, ttl_s=60)


def test_release_job_lease_propagates_redis_errors():
  class _BrokenRedis(FakeRedis):
    def evalsha(self, sha, numkeys, *args):
      raise RuntimeError("LOADING Redis is loading the dataset in memory")

  with pytest.raises(RuntimeError):
    jq.release_job_lease(
        _BrokenRedis(), kind="ingest", identity="x", owner_token=OWNER_A,
    )


def test_dedupe_enqueue_skips_queued_and_inflight_identities():
  """Repeated discover passes must not grow the append queue without bound."""
  client = FakeRedis()
  assert jq.enqueue_list_job(
      client, kind="append", identity="/raw/a", dedupe=True,
  ) == 1
  assert jq.enqueue_list_job(
      client, kind="append", identity="/raw/a", dedupe=True,
  ) == 0
  assert client.llen(jq.job_queue_key("append")) == 1

  claim = jq.claim_list_job(
      client, kind="append", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert jq.enqueue_list_job(
      client, kind="append", identity="/raw/a", dedupe=True,
  ) == 0
  assert client.llen(jq.job_queue_key("append")) == 0


def test_list_claim_conflict_puts_member_back_on_queue():
  client = FakeRedis()
  jq.enqueue_list_job(client, kind="day_close", identity="/d/2026-08-01.tar")
  client.set(
      jq.job_lease_key("day_close", "/d/2026-08-01.tar"),
      OWNER_B,
      nx=True,
      ex=60,
  )
  assert jq.claim_list_job(
      client, kind="day_close", owner_token=OWNER_A, ttl_s=60,
  ) is None
  assert client.lrange(jq.job_queue_key("day_close"), 0, -1) == [
      "/d/2026-08-01.tar",
  ]


def test_queue_capacity_limit_blocks_unbounded_growth():
  client = FakeRedis()
  for index in range(3):
    _seed_ingest(client, "p|%d|1" % index, index)
  assert jq.queue_has_capacity(client, kind="ingest", limit=4)
  assert not jq.queue_has_capacity(client, kind="ingest", limit=3)


def test_check_redis_queue_safety_flags_eviction_and_ttl():
  client = FakeRedis()
  assert jq.check_redis_queue_safety(client) == []
  client.set_config_for_tests("maxmemory-policy", "allkeys-lru")
  jq.zadd_ingest_job(client, identity="p|1|1", score=1)
  client._ttl[jq.job_queue_key("ingest")] = 30
  problems = jq.check_redis_queue_safety(client)
  assert any("maxmemory-policy" in text for text in problems)
  assert any("has a TTL" in text for text in problems)


def test_queue_census_counts_queued_and_inflight():
  client = FakeRedis()
  _seed_ingest(client, "p|1|1", 1)
  _seed_ingest(client, "p|2|1", 2)
  jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  jq.enqueue_list_job(client, kind="append", identity="/raw/a")
  census = jq.queue_census(client)
  assert census["ingest"] == {"queued": 1, "inflight": 1}
  assert census["append"] == {"queued": 1, "inflight": 0}
  assert "ingest=1/1" in jq.format_queue_census(census)


def test_count_inflight_by_band_uses_recorded_scores():
  client = FakeRedis()
  _seed_ingest(client, "hot|1|1", 5)
  _seed_ingest(client, "cold|1|1", jq.CATCHUP_SCORE_BASE + 5)
  jq.claim_ingest_job(
      client, band="hot", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  jq.claim_ingest_job(
      client, band="catchup", owner_token=OWNER_B, ttl_s=60, now_s=1000.0,
  )
  assert jq.count_inflight_by_band(client) == (1, 1)


def test_attempt_counter_increments_and_dead_letter_persists(tmp_path):
  client = FakeRedis()
  assert jq.bump_job_attempt(client, kind="ingest", identity="p|1|1") == 1
  assert jq.bump_job_attempt(client, kind="ingest", identity="p|1|1") == 2
  assert jq.read_job_attempt(client, kind="ingest", identity="p|1|1") == 2

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
  client = FakeRedis()
  jq.enqueue_list_job(client, kind="day_close", identity="/d/x.tar")
  jq.claim_list_job(
      client, kind="day_close", owner_token=OWNER_A, ttl_s=60, now_s=1000.0,
  )
  reaped = jq.reap_expired_inflight(
      client, kind="day_close", now_s=1200.0, ttl_s=60,
  )
  assert reaped == ["/d/x.tar"]
  assert client.lrange(jq.job_queue_key("day_close"), 0, -1) == ["/d/x.tar"]


def test_reap_respects_limit():
  client = FakeRedis()
  for index in range(5):
    _seed_ingest(client, "p|%d|1" % index, index)
    jq.claim_ingest_job(
        client, band="hot", owner_token="n:h:b:%d" % index, ttl_s=60,
        now_s=1000.0,
    )
  reaped = jq.reap_expired_inflight(
      client, kind="ingest", now_s=1200.0, limit=2, ttl_s=60,
  )
  assert len(reaped) == 2
  assert client.hlen(jq.job_inflight_key("ingest")) == 3
