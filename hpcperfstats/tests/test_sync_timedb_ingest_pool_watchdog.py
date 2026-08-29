"""Regression tests: coordinator ingest wall-clock watchdog is retired.

Soft-kill is idle-stall + Postgres statement_timeout. Submit-age abandon must
not reclaim slots or trigger recycle. Collateral requeue / pool recycle helpers
remain for other recycle paths.
"""
from __future__ import annotations

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


def test_ingest_watchdog_budget_retired_always_zero(tmp_path):
  path = tmp_path / "stats"
  path.write_bytes(b"x" * 1024)
  assert qo._ingest_watchdog_budget_s(str(path)) == 0.0
  assert qo._ingest_watchdog_budget_s("/missing/path") == 0.0


def test_abandon_timed_out_ingest_is_noop_even_past_budget(monkeypatch):
  """Retired watchdog must not free slots or bump attempts."""
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

  assert abandoned == []
  assert identity in inflight
  assert identity in claims
  assert submitted == {identity: 100.0}
  assert client.get(jq.job_lease_key(jq.JOB_KIND_INGEST, identity)) is not None
  assert jq.read_job_attempt(client, kind=jq.JOB_KIND_INGEST, identity=identity) == 0


def test_ingest_coordinator_loop_does_not_call_abandon():
  """Ingest-coordinator must not invoke the retired submit-age watchdog."""
  src = __import__("inspect").getsource(qo._ingest_coordinator_loop)
  assert "_abandon_timed_out_ingest" not in src


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
  assert jq.read_job_attempt(client, kind=jq.JOB_KIND_INGEST, identity=survivor) == 0


def test_pool_recycle_tolerates_terminate_failure():
  class _Pool:
    def terminate(self):
      raise OSError("already dead")

    def join(self):
      raise OSError("already dead")

  sentinel = object()
  assert qo._recycle_ingest_pool(_Pool(), factory=lambda: sentinel) is sentinel
