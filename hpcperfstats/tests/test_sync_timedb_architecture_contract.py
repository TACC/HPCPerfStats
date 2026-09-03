"""Predicate architecture contracts for the sync_timedb queue orchestrator.

B-09 (slice 4): lock rediscovered → ingest job; remaining-raw → ingest/append
job; day_close = filesystem complete + 32h min-age. Do not freeze two-queue
threads as sacred law.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from hpcperfstats.dbload.lib import sync_timedb_job_store as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def test_arch_predicate_discovered_incomplete_enqueues_ingest_job():
  """Discovered closed raw with incomplete ingest must create an ingest job."""
  client = SyncTimedbJobStore("")
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
  client = SyncTimedbJobStore("")
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
  """Empty in-process job-store queues never imply archive catch-up."""
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


def test_job_store_has_no_lua_or_redis_reap():
  """In-process reap walks memory maps; Lua/HGETALL must not return."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_job_store as jq

  src = inspect.getsource(jq)
  assert "_REAP_LUA" not in src
  assert "HGETALL" not in src
  assert "redis.call" not in src


def test_append_to_tar_refilters_under_write_lock():
  """F13: lexists filter for tar -r must run under file_write_lock."""
  import inspect
  from hpcperfstats.dbload import sync_timedb as st

  src = inspect.getsource(st._append_to_tar)
  lock_idx = src.find("file_write_lock")
  lexists_idx = src.find("os.path.lexists")
  assert lock_idx >= 0 and lexists_idx >= 0
  assert lock_idx < lexists_idx


def test_populate_queue_complete_clears_queued_day(tmp_path):
  """Populate ACK drops the ephemeral queued-day flag on the members store."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
      complete_populate_queue_job,
      enqueue_archive_members_populate,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
      SyncTimedbArchiveMembersStore,
      set_process_archive_members_store,
  )

  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  try:
    assert enqueue_archive_members_populate("/a.tar.zst", "2026-08-01") is True
    assert enqueue_archive_members_populate("/a.tar.zst", "2026-08-01") is False
    job = store.dequeue_populate(timeout_s=0.01)
    assert job["day_token"] == "2026-08-01"
    complete_populate_queue_job(job)
    assert enqueue_archive_members_populate("/a.tar.zst", "2026-08-01") is True
  finally:
    set_process_archive_members_store(None)


def test_orchestrator_boot_allows_persistence_reset():
  """Boot must auto-reset sidecars on contract mismatch (allow_reset=True)."""
  import inspect
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "allow_reset=True" in src
  assert "allow_reset=False" not in src


def test_persistence_reset_refused_when_disallowed(tmp_path):
  """allow_reset=False must fail closed on version mismatch (API still refuses)."""
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


def test_persistence_mismatch_allow_reset_true_resets(tmp_path):
  """Regression: old=7 new=current must reset, not raise (operator boot path)."""
  from hpcperfstats.dbload.lib import sync_timedb_persistence as pers

  archive = tmp_path / "a"
  archive.mkdir()
  stale = archive / ".sync_timedb_state.json"
  stale.write_text("[]", encoding="utf-8")
  pers._save_json_atomic(
      pers.persistence_contract_path(str(archive)),
      {"contract_version": 7, "written_at": 0},
  )
  assert pers.ensure_persistence_contract(str(archive), allow_reset=True) is True
  assert (
      pers._read_contract_version(str(archive))
      == pers.SYNC_TIMEDB_PERSISTENCE_CONTRACT_VERSION
  )
  assert not stale.exists()


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


_REDIS_AS_LAW_PHRASES = (
    "when redis l2 is enabled",
    "when redis is required",
    "when redis is fully warm",
    "prewarms redis",
    "cold redis",
    "warm redis",
    "redis list",
    "redis hash",
    "via redis set",
    "blocked in redis",
    "enqueue redis populate",
    "hit cold redis",
    "redis-first",
    "redis first",
    "archive spawn pool",
    "workers are separate processes under spawn",
    "no redis fallback",
    "no redis-disable fallback",
    "redis-disable",
    "redis enabled",
    "during redis wait",
    "on brpop",
    "ingest spawn pool",
    "tar append redis",
    "redis key",
    "append redis parity",
    "redis +",
    "not redis populate",
)


def _prose_outside_backticks(text: str) -> str:
  """Return markdown with fenced identifier spans removed."""
  return "".join(
      part for index, part in enumerate(text.split("`")) if index % 2 == 0
  )


def test_arch_sync_timedb_rules_have_no_redis_as_law():
  """sync-timedb-*.mdc prose must not treat Redis as the live members/job bus."""
  from pathlib import Path

  rules_dir = Path(__file__).resolve().parents[1] / "cursor-rules"
  failures: list[str] = []
  for path in sorted(rules_dir.glob("sync-timedb-*.mdc")):
    prose = _prose_outside_backticks(path.read_text(encoding="utf-8")).lower()
    for phrase in _REDIS_AS_LAW_PHRASES:
      if phrase not in prose:
        continue
      if phrase == "redis first" and "reintroduce redis" in prose:
        continue
      failures.append("%s: %r" % (path.name, phrase))
  assert not failures, "Redis-as-law leftover in domain rules: %s" % failures


_ALLOWED_REDIS_STEMS = ("rediscover", "redispatch")
_REDIS_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_]*redis[A-Za-z0-9_]*",
    re.IGNORECASE,
)


def test_arch_sync_timedb_code_has_no_redis_identifiers():
  """Live sync_timedb identifiers must not contain redis except rediscover/redispatch."""
  from pathlib import Path

  checkout = Path(__file__).resolve().parents[2]
  paths = []
  paths.extend(sorted((checkout / "hpcperfstats" / "dbload").glob("sync_timedb*.py")))
  paths.extend(
      sorted((checkout / "hpcperfstats" / "dbload" / "lib").glob("sync_timedb*.py"))
  )
  paths.append(checkout / "scripts" / "seal_open_daily_tars_to_zst.py")
  paths.append(checkout / "scripts" / "verify_open_tar_matches_zst.py")
  failures: list[str] = []
  for path in paths:
    src = path.read_text(encoding="utf-8")
    for match in _REDIS_TOKEN_RE.finditer(src):
      token = match.group(0)
      if any(stem in token.lower() for stem in _ALLOWED_REDIS_STEMS):
        continue
      failures.append("%s:%s" % (path.name, token))
  assert not failures, "leftover redis identifiers: %s" % failures
