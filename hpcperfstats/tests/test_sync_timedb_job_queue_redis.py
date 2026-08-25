"""Live Redis Lua tests for job:v1 claim, reap, and band isolation.

Host pytest skips these unless compose sets
``HPCPERFSTATS_COMPOSE_NETWORK=1`` and ``HPCPERFSTATS_PYTEST_LIVE_REDIS=1``.
Run: ``tests/run_redis_cache_pytest_workflow.sh``.

The host FakeRedis stand-in in ``fake_redis_queue.py`` reimplements Lua in
Python. These tests exercise the real scripts for loss, starvation, lease
expiry, and crash-recovery paths.
"""
from __future__ import annotations

import os
import threading
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq

_COMPOSE = os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
    "1",
    "yes",
    "true",
)
_LIVE = os.environ.get("HPCPERFSTATS_PYTEST_LIVE_REDIS", "").strip().lower() in (
    "1",
    "yes",
    "true",
)

pytestmark = pytest.mark.skipif(
    not (_COMPOSE and _LIVE),
    reason=(
        "Requires Docker Compose network and live Redis "
        "(HPCPERFSTATS_COMPOSE_NETWORK=1 and HPCPERFSTATS_PYTEST_LIVE_REDIS=1). "
        "Run: tests/run_redis_cache_pytest_workflow.sh"
    ),
)

_OWNER_A = "nonceA:host1:boot1:1001"
_OWNER_B = "nonceB:host1:boot1:1002"
_JOBQ_TEST_DB = 15


def _job_queue_test_url(base: str) -> str:
  """Rewrite the configured Redis URL onto an isolated test DB."""
  parts = urlsplit(str(base or "redis://redis:6379/1"))
  return urlunsplit(
      (parts.scheme, parts.netloc, "/%d" % _JOBQ_TEST_DB, parts.query, parts.fragment),
  )


@pytest.fixture
def client():
  """Isolated live Redis client on DB 15 with a flushed keyspace."""
  import redis as redis_lib

  from hpcperfstats.dbload.lib import conf_parser as cfg

  jq.reset_job_queue_script_cache_for_tests()
  url = _job_queue_test_url(cfg.get_redis_location())
  conn = redis_lib.from_url(
      url,
      decode_responses=True,
      socket_connect_timeout=5,
      socket_timeout=5,
  )
  conn.ping()
  conn.flushdb()
  yield conn
  conn.flushdb()
  conn.close()
  jq.reset_job_queue_script_cache_for_tests()


def _ident(label: str) -> str:
  return "/pytest-jobq/%s/%s" % (uuid.uuid4().hex, label)


@pytest.mark.live_redis
def test_live_claim_is_atomic_pop_and_lease(client):
  """Q1: a successful Lua claim leaves work in-flight under a lease, never dropped."""
  identity = _ident("atomic")
  jq.zadd_ingest_job(client, identity=identity, score=1.0)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=_OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert claim.identity == identity
  assert client.zscore(jq.job_queue_key("ingest"), identity) is None
  inflight = jq.read_inflight_entries(client, kind="ingest")
  assert identity in inflight
  assert inflight[identity][1] == _OWNER_A
  assert client.get(jq.job_lease_key("ingest", identity)) == _OWNER_A


@pytest.mark.live_redis
def test_live_lease_conflict_leaves_identity_on_zset(client):
  """Q1: a contended SET NX must not ZREM-and-drop the identity."""
  held = _ident("held")
  free = _ident("free")
  jq.zadd_ingest_job(client, identity=held, score=5.0)
  jq.zadd_ingest_job(client, identity=free, score=9.0)
  client.set(jq.job_lease_key("ingest", held), _OWNER_B, nx=True, ex=60)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=_OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert claim.identity == free
  held_score = client.zscore(jq.job_queue_key("ingest"), held)
  assert held_score == 5.0 + jq.LEASE_CONFLICT_SCORE_PENALTY


@pytest.mark.live_redis
def test_live_expired_inflight_reaped_back_onto_queue(client):
  """Q2: a crashed owner is reaped onto the ZSET with attempt-safe deprioritization."""
  identity = _ident("reap")
  jq.zadd_ingest_job(client, identity=identity, score=7.0)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token=_OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  too_early = jq.reap_expired_inflight(
      client, kind="ingest", now_s=1061.0, ttl_s=60,
  )
  assert too_early == []
  reaped = jq.reap_expired_inflight(
      client, kind="ingest", now_s=1200.0, ttl_s=60,
  )
  assert reaped == [identity]
  assert jq.read_inflight_entries(client, kind="ingest") == {}
  assert client.get(jq.job_lease_key("ingest", identity)) is None
  assert client.zscore(jq.job_queue_key("ingest"), identity) == (
      7.0 + jq.LEASE_CONFLICT_SCORE_PENALTY
  )


@pytest.mark.live_redis
def test_live_reaped_job_survives_late_ack_from_dead_owner(client):
  """Crash recovery: a late ack from the dead owner must not erase the new claim."""
  identity = _ident("late-ack")
  jq.zadd_ingest_job(client, identity=identity, score=7.0)
  first = jq.claim_ingest_job(
      client, band="hot", owner_token=_OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert first is not None
  jq.reap_expired_inflight(client, kind="ingest", now_s=1200.0, ttl_s=60)
  second = jq.claim_ingest_job(
      client, band="hot", owner_token=_OWNER_B, ttl_s=60, now_s=1200.0,
  )
  assert second is not None and second.owner_token == _OWNER_B
  assert not jq.ack_job(
      client, kind="ingest", identity=identity, owner_token=_OWNER_A,
  )
  inflight = jq.read_inflight_entries(client, kind="ingest")
  assert inflight[identity][1] == _OWNER_B
  assert client.get(jq.job_lease_key("ingest", identity)) == _OWNER_B


@pytest.mark.live_redis
def test_live_hot_range_does_not_starve_catchup(client):
  """Starvation: ranged Lua pop of hot must leave the catchup member queued."""
  hot_id = _ident("hot")
  catch_id = _ident("catch")
  jq.zadd_ingest_job(client, identity=hot_id, score=1.0)
  jq.zadd_ingest_job(
      client, identity=catch_id, score=jq.CATCHUP_SCORE_BASE + 1.0,
  )
  hot_claim = jq.claim_ingest_job(
      client, band="hot", owner_token=_OWNER_A, ttl_s=60, now_s=1000.0,
  )
  assert hot_claim is not None and hot_claim.identity == hot_id
  assert client.zscore(jq.job_queue_key("ingest"), catch_id) == (
      jq.CATCHUP_SCORE_BASE + 1.0
  )
  catch_claim = jq.claim_ingest_job(
      client, band="catchup", owner_token=_OWNER_B, ttl_s=60, now_s=1000.0,
  )
  assert catch_claim is not None and catch_claim.identity == catch_id


@pytest.mark.live_redis
def test_live_concurrent_claim_does_not_drop_identity(client):
  """Two claimants on one member: exactly one wins; work stays queued or leased."""
  identity = _ident("race")
  jq.zadd_ingest_job(client, identity=identity, score=1.0)
  results: list[object] = []
  barrier = threading.Barrier(2)

  def _claim(token: str) -> None:
    barrier.wait(timeout=5)
    results.append(
        jq.claim_ingest_job(
            client, band="hot", owner_token=token, ttl_s=60, now_s=1000.0,
        ),
    )

  threads = [
      threading.Thread(target=_claim, args=(_OWNER_A,)),
      threading.Thread(target=_claim, args=(_OWNER_B,)),
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=10)
  claimed = [item for item in results if item is not None]
  assert len(claimed) == 1
  inflight = jq.read_inflight_entries(client, kind="ingest")
  queued = client.zscore(jq.job_queue_key("ingest"), identity)
  assert identity in inflight or queued is not None
