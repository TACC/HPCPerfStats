"""Regression tests for the ingest pool watchdog (T2/T3).

A ``multiprocessing.Pool`` worker killed mid-task leaves its ``AsyncResult``
never ready, so the old drain loop held the slot and the Redis lease forever
while still reporting ``busy``. These tests lock per-entry deadlines, slot
release, requeue with ``attempt+1``, and the pool-recycle signal.
"""
from __future__ import annotations

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.tests.fake_redis_queue import FakeRedis


class _NeverReady:
  """AsyncResult stand-in for a worker whose process was killed."""

  def ready(self):
    return False

  def get(self, timeout=0):  # pragma: no cover - drain must not call this
    raise AssertionError("get() must not be called for a hung worker")


class _Ready:
  def __init__(self, result):
    self._result = result

  def ready(self):
    return True

  def get(self, timeout=0):
    del timeout
    return self._result


def _real_claim(client, identity, *, score=5.0):
  """Enqueue then atomically claim so lease/in-flight state really exists."""
  jq.zadd_ingest_job(client, identity=identity, score=score)
  band = jq.decode_ingest_band(score)
  claim = jq.claim_ingest_job(
      client, band=band, owner_token=jq.make_lease_owner_token(),
  )
  assert claim is not None and claim.identity == identity
  return claim


def _claim(identity, *, score=5.0, owner="n:h:b:1"):
  return jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token=owner,
      deadline=1.0,
      score=score,
  )


def test_ingest_deadline_uses_per_file_timeout_plus_grace(tmp_path, monkeypatch):
  path = tmp_path / "stats"
  path.write_bytes(b"x" * 1024)
  monkeypatch.setattr(
      qo.it, "resolve_ingest_per_file_timeout_s", lambda p: 300.0,
  )
  budget = qo._ingest_watchdog_budget_s(str(path))
  assert budget > 300.0
  assert budget == pytest.approx(300.0 + qo.INGEST_WATCHDOG_GRACE_S)


def test_ingest_deadline_survives_unreadable_path(monkeypatch):
  def _boom(_path):
    raise OSError("stat failed")

  monkeypatch.setattr(qo.it, "resolve_ingest_per_file_timeout_s", _boom)
  budget = qo._ingest_watchdog_budget_s("/missing/path")
  assert budget >= qo.INGEST_WATCHDOG_GRACE_S


def test_hung_worker_frees_slot_and_requeues_with_attempt_increment(monkeypatch):
  client = FakeRedis()
  identity = "/raw/a|1|1"
  claim = _real_claim(client, identity, score=5.0)
  inflight = {identity: _NeverReady()}
  claims = {identity: claim}
  submitted = {identity: 100.0}
  monkeypatch.setattr(qo, "_ingest_watchdog_budget_s", lambda _p: 60.0)

  abandoned = qo._abandon_timed_out_ingest(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      archive_data_dir="/archive",
      now=100.0 + 60.0 + 1.0,
      log_fn=lambda *a, **k: None,
  )

  assert abandoned == [identity]
  assert inflight == {}
  assert claims == {}
  assert submitted == {}
  # Lease released and job back on the ingest queue for another attempt.
  assert client.get(jq.job_lease_key(jq.JOB_KIND_INGEST, identity)) is None
  assert client.zscore(jq.job_queue_key(jq.JOB_KIND_INGEST), identity) is not None
  assert jq.read_job_attempt(client, kind=jq.JOB_KIND_INGEST, identity=identity) == 1
  assert client.hget(jq.job_inflight_key(jq.JOB_KIND_INGEST), identity) is None


def test_worker_inside_budget_is_left_alone(monkeypatch):
  client = FakeRedis()
  identity = "/raw/a|1|1"
  inflight = {identity: _NeverReady()}
  claims = {identity: _claim(identity)}
  submitted = {identity: 100.0}
  monkeypatch.setattr(qo, "_ingest_watchdog_budget_s", lambda _p: 600.0)

  abandoned = qo._abandon_timed_out_ingest(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      archive_data_dir="/archive",
      now=100.0 + 59.0,
      log_fn=lambda *a, **k: None,
  )

  assert abandoned == []
  assert identity in inflight
  assert identity in claims


def test_ready_worker_is_never_abandoned_even_past_budget(monkeypatch):
  """A finished-but-undrained result must go through the normal drain path."""
  client = FakeRedis()
  identity = "/raw/a|1|1"
  inflight = {identity: _Ready(("/raw/a", True, True, 0.1, {}))}
  claims = {identity: _claim(identity)}
  submitted = {identity: 0.0}
  monkeypatch.setattr(qo, "_ingest_watchdog_budget_s", lambda _p: 1.0)

  abandoned = qo._abandon_timed_out_ingest(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      archive_data_dir="/archive",
      now=1e9,
      log_fn=lambda *a, **k: None,
  )

  assert abandoned == []
  assert identity in inflight


def test_repeated_timeouts_dead_letter_instead_of_looping(tmp_path, monkeypatch):
  client = FakeRedis()
  identity = "/raw/a|1|1"
  monkeypatch.setattr(qo, "_ingest_watchdog_budget_s", lambda _p: 1.0)
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 2)
  archive = tmp_path / "archive"
  archive.mkdir()

  for _ in range(4):
    inflight = {identity: _NeverReady()}
    claims = {identity: _real_claim(client, identity)}
    submitted = {identity: 0.0}
    qo._abandon_timed_out_ingest(
        client,
        inflight=inflight,
        claims=claims,
        submitted=submitted,
        archive_data_dir=str(archive),
        now=1e9,
        log_fn=lambda *a, **k: None,
    )

  dead = jq.queue_dead_letter_path(str(archive))
  assert dead
  import os

  assert os.path.exists(dead)
  # Poison stops re-entering the queue once attempts are exhausted.
  assert client.zscore(jq.job_queue_key(jq.JOB_KIND_INGEST), identity) is None


def test_drain_ingest_ready_clears_submitted_timestamps():
  client = FakeRedis()
  identity = "/raw/a|1|1"
  inflight = {identity: _Ready(("/raw/a", True, True, 0.1, {}))}
  claims = {identity: _real_claim(client, identity)}
  submitted = {identity: 0.0}

  done = qo._drain_ingest_ready(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      tgz_archive_dir="/daily",
      archive_data_dir="/archive",
  )

  assert done == 1
  # A stale submitted entry would make the watchdog abandon a future claim
  # that happens to reuse the same identity.
  assert submitted == {}


def test_fill_ingest_band_records_submission_time(monkeypatch, tmp_path):
  client = FakeRedis()
  path = tmp_path / "raw"
  path.write_text("x", encoding="utf-8")
  identity = "%s|1|1" % path
  jq.zadd_ingest_job(client, identity=identity, score=-1.0)

  class _Pool:
    def apply_async(self, fn, args):
      del fn, args
      return _NeverReady()

  inflight: dict[str, object] = {}
  claims: dict[str, object] = {}
  submitted: dict[str, float] = {}
  monkeypatch.setattr(qo, "_path_from_ingest_identity", lambda ident: str(path))

  qo._fill_ingest_band(
      client,
      band="hot",
      cap=1,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      ingest_pool=_Pool(),
      manager_lock=None,
  )

  assert list(submitted) == list(inflight)
  assert all(value > 0 for value in submitted.values())


def test_pool_recycle_replaces_pool_after_abandonment():
  """Recycling must terminate the old pool and hand back a fresh one."""
  events = []

  class _Pool:
    def __init__(self, tag):
      self.tag = tag

    def terminate(self):
      events.append(("terminate", self.tag))

    def join(self):
      events.append(("join", self.tag))

  old = _Pool("old")
  new_pool = _Pool("new")
  result = qo._recycle_ingest_pool(old, factory=lambda: new_pool)
  assert result is new_pool
  assert events == [("terminate", "old"), ("join", "old")]


def test_recycle_requeues_healthy_survivors_without_burning_an_attempt():
  """Terminating the pool kills every worker, not just the hung one."""
  client = FakeRedis()
  survivor = "/raw/live|1|1"
  claim = _real_claim(client, survivor, score=-3.0)
  inflight = {survivor: _NeverReady()}
  claims = {survivor: claim}
  submitted = {survivor: 0.0}

  requeued = qo._requeue_pool_collateral(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      log_fn=lambda *a, **k: None,
  )

  assert requeued == 1
  assert inflight == {} and claims == {} and submitted == {}
  assert client.zscore(jq.job_queue_key(jq.JOB_KIND_INGEST), survivor) == -3.0
  # Preemption is not the job's fault, so its retry budget is untouched.
  assert jq.read_job_attempt(client, kind=jq.JOB_KIND_INGEST, identity=survivor) == 0


def test_pool_recycle_tolerates_terminate_failure():
  class _Pool:
    def terminate(self):
      raise OSError("already dead")

    def join(self):
      raise OSError("already dead")

  sentinel = object()
  assert qo._recycle_ingest_pool(_Pool(), factory=lambda: sentinel) is sentinel
