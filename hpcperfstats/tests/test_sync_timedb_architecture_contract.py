"""Predicate architecture contracts for the sync_timedb queue orchestrator.

B-09 (slice 4): lock rediscovered → ingest job; remaining-raw → ingest/append
job; day_close = filesystem complete + 32h min-age. Do not freeze two-queue
threads as sacred law.
"""
from __future__ import annotations

from datetime import date, datetime

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
from hpcperfstats.tests.fake_redis_queue import FakeRedis


def test_arch_predicate_discovered_incomplete_enqueues_ingest_job():
  """Discovered closed raw with incomplete ingest must create an ingest job."""
  client = FakeRedis()
  plan = jr.classify_closed_raw_path(
      "/raw/host/1",
      tgz_archive_dir="/daily",
      size=10,
      mtime_ns=20,
      calendar_day=date(2026, 8, 20),
      ingest_is_complete_fn=lambda **k: False,
      append_is_complete_fn=lambda **k: True,
  )
  assert plan.needs_ingest is True
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client, plan, today=date(2026, 8, 24), hot_days=8
  )
  assert enqueued["ingest"] is True
  ingest_key = jq.job_queue_key(jq.JOB_KIND_INGEST)
  assert client.zscore(ingest_key, plan.identity) is not None


def test_arch_predicate_remaining_raw_enqueues_ingest_or_append_job():
  """Remaining-raw incomplete append must leave an append LIST job."""
  client = FakeRedis()
  plan = jr.classify_closed_raw_path(
      "/raw/host/2",
      tgz_archive_dir="/daily",
      size=11,
      mtime_ns=21,
      calendar_day=date(2026, 6, 1),
      ingest_is_complete_fn=lambda **k: True,
      append_is_complete_fn=lambda **k: False,
  )
  assert plan.needs_append is True
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client, plan, today=date(2026, 8, 24), hot_days=2
  )
  assert enqueued["append"] is True
  assert plan.path in client.lrange(
      jq.job_queue_key(jq.JOB_KIND_APPEND), 0, -1,
  )


def test_arch_predicate_day_close_is_filesystem_plus_min_age():
  """day_close complete requires filesystem complete and 32h min-age."""
  assert jr.day_close_is_complete(
      "/daily/2026-08-01.tar",
      calendar_day=date(2026, 8, 1),
      filesystem_complete=True,
      min_age_elapsed=True,
      phase_name="done",
  )
  assert not jr.day_close_is_complete(
      "/daily/2026-08-01.tar",
      calendar_day=date(2026, 8, 1),
      filesystem_complete=True,
      min_age_elapsed=False,
      phase_name="done",
  )
  assert not jr.day_close_is_complete(
      "/daily/2026-08-01.tar",
      calendar_day=date(2026, 8, 1),
      filesystem_complete=False,
      min_age_elapsed=True,
  )
  assert jr.day_close_min_age_elapsed(
      date(2026, 8, 1),
      now=datetime(2026, 8, 3, 12, 0, 0),
      min_age_hours=32.0,
  )
  assert not jr.day_close_min_age_elapsed(
      date(2026, 8, 1),
      now=datetime(2026, 8, 2, 0, 0, 0),
      min_age_hours=32.0,
  )


def test_arch_empty_job_queues_do_not_mean_caught_up():
  """Empty Redis job structures never imply archive catch-up."""
  assert jr.empty_job_queues_mean_caught_up() is False


def test_arch_checkpoint_sidecar_not_reconstruct_source_of_truth():
  """``.sync_timedb_state.json`` is not reconstruct source of truth."""
  assert jr.checkpoint_sidecar_is_reconstruct_source_of_truth() is False
  assert "disk" in jr.reconstruct_sources_of_truth()


def test_oq1_no_heartbeat_renew_in_orchestrator_source():
  """OQ-1: orchestrator must not renew job leases on every busy tick."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "_renew_active_claims" not in src


def test_handoff_priority_paths_not_in_orchestrator():
  """B drop: orchestrator must not use handoff_priority_paths pins."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  src = inspect.getsource(qo)
  assert "handoff_priority_paths" not in src


def test_handoff_priority_paths_removed_from_legacy_helpers():
  """P-D: residual B handoff pins must not remain on processed-path helpers."""
  import inspect
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  assert "handoff_priority_paths" not in inspect.signature(
      st._add_processed_path,
  ).parameters
  assert "handoff_priority_paths" not in inspect.signature(
      st._transition_file_state,
  ).parameters
  assert "handoff_priority_paths" not in inspect.signature(
      ah.cap_pending_stats_with_blocked_retention,
  ).parameters


def test_reap_lua_never_hgetall():
  """F7: expired inflight recovery must not HGETALL the full hash."""
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq

  assert "HGETALL" not in jq._REAP_LUA
  assert "HSCAN" in jq._REAP_LUA


def test_append_to_tar_refilters_under_write_lock():
  """F13: lexists filter for tar -r must run under file_write_lock."""
  import inspect
  from hpcperfstats.dbload import sync_timedb as st

  src = inspect.getsource(st._append_to_tar)
  lock_idx = src.find("file_write_lock")
  lexists_idx = src.find("os.path.lexists")
  assert lock_idx >= 0 and lexists_idx >= 0
  assert lock_idx < lexists_idx


def test_populate_processing_ack_parks_inflight_until_complete(monkeypatch):
  """F5: claim must park inflight and only clear NX on complete."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_members_redis as amr

  class _C:
    def __init__(self):
      self.inflight = []
      self.queued_cleared = []

    def rpush(self, key, raw):
      self.inflight.append((key, raw))
      return 1

    def lrem(self, key, count, raw):
      self.inflight = [x for x in self.inflight if x != (key, raw)]
      return 1

  client = _C()
  monkeypatch.setattr(amr, "get_archive_members_redis_client", lambda **k: client)
  cleared = []

  def _clear(day):
    cleared.append(day)

  monkeypatch.setattr(amr, "clear_populate_queued", _clear)
  job = {"day_token": "2026-08-01", "canonical": "/a.tar.zst"}
  raw = '{"day_token":"2026-08-01"}'
  out = amr._claim_populate_queue_job(job, raw=raw)
  assert out is job
  assert client.inflight
  assert cleared == []
  amr.complete_populate_queue_job({**job, "_queue_raw": raw})
  assert cleared == ["2026-08-01"]
  assert not client.inflight


def test_orchestrator_boot_refuses_persistence_reset():
  """P-C: orchestrator must call ensure_persistence_contract(allow_reset=False)."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "allow_reset=False" in src


def test_persistence_reset_refused_when_disallowed(tmp_path):
  """allow_reset=False must fail closed on version mismatch."""
  import pytest
  from hpcperfstats.dbload.lib import sync_timedb_persistence as pers
  archive = tmp_path / "a"
  archive.mkdir()
  pers._save_json_atomic(
      pers.persistence_contract_path(str(archive)),
      {"contract_version": -1, "written_at": 0},
  )
  with pytest.raises(pers.PersistenceContractMismatchError):
    pers.ensure_persistence_contract(
        str(archive), allow_reset=False,
    )
  assert pers._read_contract_version(str(archive)) == -1


def test_persistence_missing_contract_allow_reset_false_initializes(tmp_path):
  """Fresh archive with allow_reset=False must write the contract, not raise."""
  from hpcperfstats.dbload.lib import sync_timedb_persistence as pers

  archive = tmp_path / "a"
  archive.mkdir()
  assert pers.ensure_persistence_contract(str(archive), allow_reset=False) is False
  assert (
      pers._read_contract_version(str(archive))
      == pers.SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  )


def test_mutable_tar_authority_uses_tar_tvf_not_tarfile():
  """P1-11: open-tar membership maps must come from GNU tar tvf."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  src = inspect.getsource(ah._read_tar_file_member_sizes_unlocked)
  assert "tvf" in src
  assert "tarfile.open" not in src


def test_orchestrator_omits_b_pending_cap_and_heartbeat_renew():
  """P1-21 / P1-22: production loop must not restore B pending-cap or renew."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "_renew_active_claims" not in src
  assert "cap_pending_stats" not in src
  assert "_add_processed_path" not in src


def test_ingest_worker_raises_session_statement_timeout():
  """P1-20: ingest workers must lift Postgres statement_timeout to the file budget."""
  import inspect
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  assert "_apply_ingest_session_statement_timeout" in inspect.getsource(
      qo._ingest_worker,
  )
  src = inspect.getsource(st._apply_ingest_session_statement_timeout)
  assert "SET statement_timeout" in src
  assert "get_sync_ingest_per_file_timeout_max_s" in src


def test_arch_no_internal_wall_timers_append_and_ingest():
  """Forbidden: wall soft-kill on tar append / ingest timed / stall abort."""
  import inspect
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ito

  append_src = inspect.getsource(st._append_to_tar)
  timed_src = inspect.getsource(st._run_ingest_timed)
  assert "run_subprocess_with_progress" in append_src
  assert "timeout=3600" not in append_src
  assert "setitimer" not in timed_src
  assert "signal.alarm" not in timed_src
  assert ito.stall_abort_polls_for_paths(["/x"]) == 0
  assert ito.resolve_ingest_per_file_timeout_s("/x") == 0.0
