"""Predicate architecture contracts for the sync_timedb queue orchestrator.

B-09 (slice 4): lock rediscovered -> ingest job; remaining-raw -> ingest/append
job; day_close = filesystem complete + 32h min-age. Do not freeze two-queue
threads as sacred law.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from hpcperfstats.dbload.lib import (
  sync_timedb_job_reconstruct as jr,
  sync_timedb_job_store as jq,
)
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
  assert client.ingest_score(plan.identity) is not None


def test_loaded_snapshot_overlay_reconstruct_enqueues_missing_work(tmp_path):
  """A persisted snapshot must not hide reconstruct-discovered missing ingest."""
  archive = str(tmp_path / "archive")
  store = SyncTimedbJobStore(archive)
  jq.zadd_ingest_job(store, identity="/raw/already", score=1.0)
  store.persist(force=True)
  revived = SyncTimedbJobStore(archive)
  plan = jr.classify_closed_raw_path(
    "/raw/missing",
    tgz_archive_dir="/daily",
    size=10,
    mtime_ns=20,
    calendar_day=date(2026, 8, 20),
    ingest_is_complete_fn=lambda **k: False,
    append_is_complete_fn=lambda **k: True,
  )
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
    revived, plan, today=date(2026, 8, 24), hot_days=8
  )
  assert enqueued["ingest"] is True
  assert revived.ingest_score(plan.identity) is not None
  assert "/raw/already" in revived.ingest_identities()


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
  assert plan.path in client.list_slice(
    "append",
    0,
    -1,
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

  src = inspect.getsource(qo)
  assert "def _renew_active_claims" not in src
  assert "_renew_active_claims" not in inspect.getsource(
    qo.run_sync_timedb_queue_orchestrator,
  )


def test_handoff_priority_paths_not_in_orchestrator():
  """B drop: orchestrator must not use handoff_priority_paths pins."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo)
  assert "handoff_priority_paths" not in src


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
      str(archive),
      allow_reset=False,
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
  assert (
    pers.ensure_persistence_contract(str(archive), allow_reset=True) is True
  )
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
  assert (
    pers.ensure_persistence_contract(str(archive), allow_reset=False) is False
  )
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
  assert "_run_gnu_tvf_file_members" in src


def test_populate_members_from_tar_scan_uses_tvf_not_tarfile():
  """Open-tar populate census must use GNU tar tvf, not tarfile."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  src = inspect.getsource(ah._populate_members_from_tar_scan)
  assert "_run_gnu_tvf_file_members" in src
  assert "tarfile.open" not in src


def test_stream_compressed_archive_members_uses_gnu_tvf_not_tarfile():
  """Sealed census must use GNU tar tvf, not tarfile / _open_tarfile_for_read."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  src = inspect.getsource(ah._stream_compressed_archive_members)
  assert "_run_gnu_tvf_file_members" in src
  assert "tarfile.open" not in src
  assert "_open_tarfile_for_read" not in src
  assert not hasattr(ah, "_open_tarfile_for_read")


def test_iter_sealed_daily_archive_uses_gnu_extract_not_tarfile():
  """Sealed member extract must use GNU tar -x, not tarfile extractfile."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  spool_src = inspect.getsource(ah._iter_sealed_daily_archive_spooled_members)
  assert "_gnu_tar_extract_named_member" in spool_src
  assert "_run_gnu_tvf_file_members" in spool_src
  assert "extractfile" not in spool_src
  assert "tarfile.open" not in spool_src
  for fn in (
    ah.iter_sealed_daily_archive_member_lines,
    ah.iter_sealed_daily_archive_member_paths,
  ):
    src = inspect.getsource(fn)
    assert "_iter_sealed_daily_archive_spooled_members" in src
    assert "extractfile" not in src
    assert "_open_tarfile_for_read" not in src
    assert "tarfile.open" not in src


def test_iter_tar_file_tasks_uses_tf_not_tarfile():
  """iter_tar_file_tasks must list via GNU tar tf, not tarfile."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  src = inspect.getsource(ah.iter_tar_file_tasks)
  assert "_run_gnu_tf_member_names" in src
  assert "tarfile.open" not in src


def test_reconcile_open_tar_has_no_tarfile_member_map_fallback():
  """Reconcile must not fall back to Python tarfile after GNU despite-fail."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  assert not hasattr(ah, "_tarfile_member_map")
  src = inspect.getsource(ah.reconcile_open_tar_with_sealed_zst)
  assert "_tarfile_member_map" not in src


def test_orchestrator_omits_b_pending_cap_and_heartbeat_renew():
  """P1-21 / P1-22: production loop must not restore B pending-cap or renew."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "def _renew_active_claims" not in inspect.getsource(qo)
  assert "_renew_active_claims" not in src
  assert "cap_pending_stats" not in src
  assert "_add_processed_path" not in src


def test_ingest_worker_raises_session_statement_timeout():
  """P1-20: ingest workers must lift Postgres statement_timeout to the file budget."""
  import inspect

  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  assert "_apply_ingest_session_statement_timeout" in inspect.getsource(
    qo._ingest_parse_worker,
  )
  src = inspect.getsource(st._apply_ingest_session_statement_timeout)
  assert "SET statement_timeout" in src
  assert "get_sync_ingest_per_file_timeout_max_s" in src


def test_orchestrator_write_stage_raises_session_statement_timeout():
  """Write-stage threads must lift statement_timeout (host_data ON CONFLICT INSERT)."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "worker_setup=_apply_ingest_session_statement_timeout" in src
  assert "start_ingest_write_stage" in src


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
  assert not hasattr(ito, "stall_abort_polls_for_paths")
  assert not hasattr(ito, "resolve_ingest_per_file_timeout_s")


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
      failures.append(f"{path.name}: {phrase!r}")
  assert not failures, f"Redis-as-law leftover in domain rules: {failures}"


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
  paths.extend(
    sorted((checkout / "hpcperfstats" / "dbload").glob("sync_timedb*.py"))
  )
  paths.extend(
    sorted(
      (checkout / "hpcperfstats" / "dbload" / "lib").glob("sync_timedb*.py")
    )
  )
  paths.append(checkout / "scripts" / "seal_open_daily_tars_to_zst.py")
  paths.append(checkout / "scripts" / "verify_open_tar_matches_zst.py")
  paths.append(checkout / "hpcperfstats" / "dbload" / "lib" / "zstd_cli.py")
  leftover_files = (
    checkout / "hpcperfstats" / "tests" / "fake_redis_queue.py",
    checkout / "hpcperfstats" / "tests" / "test_sync_timedb_job_queue_redis.py",
    checkout
    / "hpcperfstats"
    / "dbload"
    / "lib"
    / "sync_timedb_archive_members_redis.py",
  )
  failures: list[str] = []
  for leftover in leftover_files:
    if leftover.exists():
      failures.append(f"leftover unused file still exists: {leftover.name}")
  for path in paths:
    src = path.read_text(encoding="utf-8")
    for match in _REDIS_TOKEN_RE.finditer(src):
      token = match.group(0)
      if any(stem in token.lower() for stem in _ALLOWED_REDIS_STEMS):
        continue
      failures.append(f"{path.name}:{token}")
  assert not failures, f"leftover redis identifiers: {failures}"


def test_day_remainder_inc_dec_same_day_for_cross_midnight_file():
  """DE-32: enqueue and ACK must count the same day for one file.

  A segment named for 23:59 on day D whose first parsed sample falls on D+1
  used to count +1 on D+1 (plan calendar day) and -1 on D (filename epoch),
  leaving D+1 permanently positive and D clamped at zero.
  """
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
    day_remainder,
    reset_day_remainder_for_tests,
  )

  reset_day_remainder_for_tests()
  epoch = int(datetime(2026, 8, 20, 23, 59, 30).timestamp())
  path = f"/raw/host/{epoch}"
  client = SyncTimedbJobStore("")
  plan = jr.classify_closed_raw_path(
    path,
    tgz_archive_dir="/daily",
    size=10,
    mtime_ns=20,
    calendar_day=date(2026, 8, 21),  # first sample after midnight
    ingest_is_complete_fn=lambda **k: False,
    append_is_complete_fn=lambda **k: True,
  )
  jr.enqueue_reconstruct_jobs_for_closed_path(
    client, plan, today=date(2026, 10, 1)
  )
  assert day_remainder(jq.JOB_KIND_INGEST, "2026-08-20") == 1
  assert day_remainder(jq.JOB_KIND_INGEST, "2026-08-21") == 0
  qo._release_day_remainder(jq.JOB_KIND_INGEST, plan.identity)
  assert day_remainder(jq.JOB_KIND_INGEST, "2026-08-20") == 0
  assert day_remainder(jq.JOB_KIND_INGEST, "2026-08-21") == 0


def test_day_remainder_rebuilt_from_job_store_at_boot(tmp_path):
  """DE-32: after a restart the counter must reflect the persisted store."""
  from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
    day_remainder,
    rebuild_day_remainders_from_job_store,
    reset_day_remainder_for_tests,
  )

  day = datetime(2026, 9, 26, 12, 0, 0)
  store = SyncTimedbJobStore(str(tmp_path))
  for i in range(3):
    store.zadd_ingest(f"/raw/h/{int(day.timestamp()) + i}", 1.0)
  store.enqueue_list("append", f"/raw/h/{int(day.timestamp()) + 9}")
  store.persist(force=True)
  store.close()
  reset_day_remainder_for_tests()  # process restart: memory counter is empty

  revived = SyncTimedbJobStore(str(tmp_path))
  assert day_remainder(jq.JOB_KIND_INGEST, "2026-09-26") == 0
  counts = rebuild_day_remainders_from_job_store(revived)
  assert counts == {"ingest": 3, "append": 1}
  assert day_remainder(jq.JOB_KIND_INGEST, "2026-09-26") == 3
  assert day_remainder(jq.JOB_KIND_APPEND, "2026-09-26") == 1
  revived.close()


def test_day_close_without_tar_hands_off_remaining_raw(tmp_path, monkeypatch):
  """DE-24: a day with closed raw but no open tar must still hand raw off."""
  import os

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
    lambda *a, **k: None,
  )
  handed = []

  class _Coord:
    def __init__(self, **_kw):
      pass

    def requeue_closed_raw_paths_for_ingest(self, tar_path, *, reason):
      handed.append((os.path.basename(tar_path), reason))
      return ["/raw/h/1"]

    def run_post_seal_verify_sync(self, _tar):
      return False

    def apply_batch_delete(self, _tar):
      return 0

    def has_closed_raw_on_disk(self, _tar):
      return True

    def phase(self, _tar):
      return "verifying"

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    _Coord,
  )
  outcome = qo._run_day_close_job(
    "2020-01-07",
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
    log_fn=lambda *a, **k: None,
  )
  assert handed == [("2020-01-07.tar", "no_open_tar")]
  assert outcome == "incomplete_raw"


def test_daily_dir_scan_enqueues_zst_only_day_with_open_manifest(tmp_path):
  """DE-24: zst-only days with an open day-removal manifest get day_close."""
  import json

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  manifests = tmp_path / ".sync_timedb_day_raw_removal"
  manifests.mkdir()
  for day, phase in (
    ("2020-02-01", "verification_complete"),
    ("2020-02-02", "done"),
  ):
    (daily / f"{day}.tar.zst").write_bytes(b"zst")
    (manifests / f"{day}.json").write_text(
      json.dumps({"phase": phase, "entries": {}, "tar_path": "x"})
    )
  client = SyncTimedbJobStore("")
  n = qo._enqueue_day_closes_for_daily_dir(
    client,
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
  )
  queued = client.list_slice(jq.JOB_KIND_DAY_CLOSE, 0, -1)
  assert n == 1
  assert [q for q in queued if "2020-02-01" in q]
  assert not [q for q in queued if "2020-02-02" in q]


def test_wait_on_ingest_uses_per_day_append_remainder(tmp_path):
  """DE-27: another day's queued appends must not hold this day."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
    note_day_remainder,
    reset_day_remainder_for_tests,
  )

  reset_day_remainder_for_tests()
  store = SyncTimedbJobStore("")
  other = f"/raw/h/{int(datetime(2020, 3, 2, 12).timestamp())}"
  jq.enqueue_list_job(store, kind=jq.JOB_KIND_APPEND, identity=other)
  note_day_remainder(jq.JOB_KIND_APPEND, "2020-03-02", 1)
  tar = str(tmp_path / "2020-03-01.tar")
  try:
    assert store.queued_count(jq.JOB_KIND_APPEND) == 1
    assert (
      qo._day_close_append_or_hot_active(store, tar, str(tmp_path)) is False
    )
    note_day_remainder(jq.JOB_KIND_APPEND, "2020-03-01", 1)
    assert qo._day_close_append_or_hot_active(store, tar, str(tmp_path)) is True
  finally:
    reset_day_remainder_for_tests()


# --- A2 / A8 / G1 / G2 bounded negative exits ---


def test_handoff_respects_dead_letter_cooldown(tmp_path):
  """DE-1: day_close handoff must not bypass a dead-letter cooldown."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  epoch = int(datetime(2022, 3, 3, 12).timestamp())
  raw = tmp_path / "h" / str(epoch)
  raw.parent.mkdir()
  raw.write_text("x")
  jq.upsert_queue_dead_letter(
    str(tmp_path),
    kind="ingest",
    identity=str(raw),
    attempt=5,
    reason="PermissionError",
  )
  client = SyncTimedbJobStore("")
  n = qo._handoff_retryable_paths_to_ingest(
    client,
    str(tmp_path / "2022-03-03.tar"),
    [str(raw)],
    tgz_archive_dir=str(tmp_path),
    archive_data_dir=str(tmp_path),
    reason="unit",
    ingest_is_complete_fn=lambda **k: False,
    append_is_complete_fn=lambda **k: True,
  )
  assert n == 0
  assert client.queued_count(jq.JOB_KIND_INGEST) == 0


def test_day_close_exception_bumps_attempt(monkeypatch, tmp_path):
  """DE-12: a day_close exception bumps attempts (no hot unbumped loop)."""
  from concurrent.futures import Future

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  monkeypatch.setattr(
    qo,
    "_run_day_close_job",
    lambda *a, **k: (_ for _ in ()).throw(KeyError("x")),
  )
  assert qo._run_day_close_job.__name__  # patched only for clarity below
  seen = []
  monkeypatch.setattr(
    jq, "bump_job_attempt", lambda *a, **k: seen.append("bump") or 1
  )
  monkeypatch.setattr(jq, "requeue_job", lambda *a, **k: seen.append("requeue"))
  fut: Future = Future()
  fut.set_result("error")
  ident = str(tmp_path / "2020-01-09.tar")
  claim = jq.ClaimedJob(
    kind=jq.JOB_KIND_DAY_CLOSE,
    identity=ident,
    owner_token="n:h:b:1",
    deadline=1.0,
    score=0.0,
  )
  qo._drain_day_close_ready(
    SyncTimedbJobStore(""),
    inflight={ident: fut},
    leases={ident: claim},
    archive_data_dir=str(tmp_path),
    log_fn=lambda *a, **k: None,
  )
  assert seen == ["bump", "requeue"]


def test_day_close_entry_gate_ignores_cooldown_identity(tmp_path):
  """A2: a dead-lettered (cooling down) identity does not hold its day."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  epoch = int(datetime(2022, 6, 28, 12).timestamp())
  ident = f"/raw/h/{epoch}"
  jq.upsert_queue_dead_letter(
    str(tmp_path), kind="ingest", identity=ident, attempt=5, reason="x"
  )
  store = SyncTimedbJobStore("")
  assert not qo._day_close_ingest_jobs_active_for_day(
    store, "2022-06-28", str(tmp_path)
  )


def test_single_stuck_file_day_reaches_terminal_resolution(tmp_path):
  """02 single-file days: cycles end in a quarantine and the raw leaves."""
  from types import SimpleNamespace

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  epoch = int(datetime(2022, 3, 3, 12).timestamp())
  raw = tmp_path / "h" / str(epoch)
  raw.parent.mkdir()
  raw.write_text("")
  store = SyncTimedbJobStore("")
  now = 1_000_000.0
  outcome = ""
  for _cycle in range(10):
    for _attempt in range(jq.job_max_attempts()):
      outcome = qo._retry_or_dead_letter(
        store,
        kind="ingest",
        claim=SimpleNamespace(identity=str(raw), owner_token="t", score=1.0),
        archive_data_dir=str(tmp_path),
        reason="PermissionError",
        log_fn=lambda *a, **k: None,
      )
    if outcome == "dead_letter_terminal":
      break
    now += 10 * 86400
    qo._readmit_due_dead_letters(store, str(tmp_path), now=now)
  assert outcome == "dead_letter_terminal"
  assert not raw.exists(), "the day can close"


def test_operational_error_requeues_not_dropped(monkeypatch, tmp_path):
  """A8/DE-14: OperationalError requeues; it is never ACK-dropped."""
  from django.db.utils import OperationalError

  from hpcperfstats.tests.test_sync_timedb_queue_orchestrator import (
    _drain_one_ingest_failure,
  )

  identity, seen = _drain_one_ingest_failure(
    monkeypatch, tmp_path, OperationalError("server closed the connection")
  )
  assert seen["acks"] == []
  assert seen["requeues"] == [identity]
  assert seen["bumps"] == []


def test_noprogress_counter_escalates_unknown_livelock(monkeypatch, tmp_path):
  """G1: an endless no-bump requeue escalates into the dead-letter cooldown."""
  from types import SimpleNamespace

  from hpcperfstats.dbload.lib import (
    conf_parser as cfg,
    sync_timedb_queue_orchestrator as qo,
  )

  monkeypatch.setattr(cfg, "get_sync_noprogress_escalate_after", lambda: 4)
  store = SyncTimedbJobStore("")
  claim = SimpleNamespace(
    kind="ingest", identity="/raw/h/1700000000", owner_token="t", score=1.0
  )
  results = [
    qo._requeue_bounded_without_attempt_bump(
      store,
      kind="ingest",
      claim=claim,
      counter="env_defer",
      cap=cfg.get_sync_noprogress_escalate_after(),
      reason="env:EAGAIN",
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
    )
    for _ in range(4)
  ]
  assert results[:3] == ["requeued"] * 3
  assert results[3] == "dead_letter"
  entry = jq._dead_letter_index(str(tmp_path))[("ingest", claim.identity)]
  assert entry["env"] is True, "environment schedule, not file attempts"


def test_noprogress_resets_on_real_progress(tmp_path):
  """G1: a successful ACK drops the counter with the payload."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  store = SyncTimedbJobStore("")
  ident = "/raw/h/1700000001"
  day, today = date(2023, 11, 14), date(2026, 10, 1)
  band = jr.select_ingest_band(day, today=today, hot_days=1)
  score = jq.encode_ingest_score(
    band=band, day=day, today=today, identity=ident
  )

  def _claim():
    store.zadd_ingest(ident, score)
    return jq.claim_ingest_job(store, band=band, owner_token="n:h:b:1")

  qo._requeue_bounded_without_attempt_bump(
    store,
    kind="ingest",
    claim=_claim(),
    counter="env_defer",
    cap=10,
    reason="env:x",
    archive_data_dir="",
    log_fn=lambda *a, **k: None,
  )
  assert (
    jq.read_job_payload_field(
      store, kind="ingest", identity=ident, name="env_defer"
    )
    == "1"
  )
  claim = jq.claim_ingest_job(store, band=band, owner_token="n:h:b:2")
  assert claim is not None
  assert store.ack(kind="ingest", identity=ident, owner_token=claim.owner_token)
  assert (
    jq.read_job_payload_field(
      store, kind="ingest", identity=ident, name="env_defer"
    )
    == ""
  )


def test_every_negative_value_has_exit_entry():
  """G2: every negative status/kind defined in code has an explicit exit."""
  from hpcperfstats.dbload.lib import (
    sync_timedb_archive_helpers as ah,
    sync_timedb_day_raw_removal as drr,
  )
  from hpcperfstats.dbload.lib.sync_timedb_negative_states import (
    SYNC_TIMEDB_NEGATIVE_STATE_EXITS as table,
  )

  statuses = (
    set(drr.RETRYABLE_SKIP_STATUSES)
    | set(drr.REPAIR_REQUIRED_STATUSES)
    | set(drr.QUARANTINE_SKIP_STATUSES)
  )
  assert statuses <= set(table["day_removal_status"])
  kinds = {getattr(ah, n) for n in dir(ah) if n.startswith("SKIP_KIND_")}
  assert kinds and kinds <= set(table["day_skip_kind"])
  assert {"timeout", "lookup_budget", "ingest_mark_failed", "ingested"} <= set(
    table["ingest_outcome"]
  )
  assert {"yielded", "incomplete_raw", "error", "verify_failed"} <= set(
    table["day_close_outcome"]
  )


def test_unknown_negative_value_takes_default_exit():
  """G2: an unknown value takes the bounded default, never 'stay forever'."""
  from hpcperfstats.dbload.lib.sync_timedb_negative_states import (
    DEFAULT_NEGATIVE_EXIT,
    negative_state_exit,
  )

  assert (
    negative_state_exit("ingest_outcome", "brand_new") == DEFAULT_NEGATIVE_EXIT
  )
  assert negative_state_exit("no_such_domain", "x") == DEFAULT_NEGATIVE_EXIT
  assert DEFAULT_NEGATIVE_EXIT == "retry_or_dead_letter"


def test_seal_with_raw_only_when_all_remaining_raw_in_tar(
  tmp_path, monkeypatch
):
  """DE-26 seal gate: seal with raw remaining only if all of it is in the tar.

  A remaining raw file that is not yet appended keeps the no-seal rule, so a
  whole tar is not recompressed per late append; once every remaining raw
  file is a same-size tar member the day seals (and can then delete it all).
  """
  import tarfile

  from hpcperfstats.dbload.lib import (
    sync_timedb_archive_helpers as ah,
    sync_timedb_queue_orchestrator as qo,
  )

  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-10.tar"
  raw_in = tmp_path / "h" / "1578657600"
  raw_late = tmp_path / "h" / "1578661200"
  raw_in.parent.mkdir()
  raw_in.write_text("in tar")
  raw_late.write_text("not appended yet")
  with tarfile.open(tar, "w") as tf:
    tf.add(str(raw_in), arcname=str(raw_in).lstrip("/"))
  ah.invalidate_mutable_tar_authority_members(str(tar))
  remaining = {"paths": [str(raw_in), str(raw_late)]}
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    qo, "_day_close_append_or_hot_active", lambda *a, **k: False
  )
  calls = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
    lambda *a, **k: calls.append(k["only_when_no_remaining_raw"]),
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def phase(self, _t):
      return "verification_complete"

    def run_pre_seal_verify_sync(self, _t):
      return True

    def run_post_seal_verify_sync(self, _t):
      return False

    def apply_batch_delete(self, _t):
      return 0

    def has_closed_raw_on_disk(self, _t):
      return True

    def closed_raw_paths_on_disk(self, _t):
      return list(remaining["paths"])

    def remaining_raw_paths_blocking_tar_drop(self, _t):
      return {str(tar) + ".zst": list(remaining["paths"])}

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    _Coord,
  )
  monkeypatch.setattr(
    qo,
    "reconcile_open_tar_with_sealed_zst",
    lambda *a, **k: None,
    raising=False,
  )

  logs = []

  def _claim():
    qo._run_day_close_job(
      "2020-01-10",
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **_k: logs.append(msg),
    )

  _claim()  # raw_late not in tar: seal is not even attempted
  # hpcperfstats04 2026-10-28: the skip_merge placeholder never blocked the
  # seal, so it ran while 4k-20k handed-off files were still being appended.
  assert calls == []
  assert any(
    "stage=seal result=ok reason=deferred_raw_not_in_tar" in m for m in logs
  )
  remaining["paths"] = [str(raw_in)]
  _claim()  # all remaining raw is in the tar: seal
  assert calls == [False]


def _append_jobs(n, day=(2026, 10, 14)):
  base = int(datetime(*day, 12).timestamp())
  return [
    jq.ClaimedJob(
      kind=jq.JOB_KIND_APPEND,
      identity=f"/raw/h/{base + i}",
      owner_token=f"n:h:b:{i}",
      deadline=1.0,
      score=0.0,
    )
    for i in range(n)
  ]


def test_append_batch_failure_does_not_dead_letter_every_path(tmp_path):
  """DE-18: one failed batch requeues its 256 paths without per-path bumps."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  store = SyncTimedbJobStore("")
  tar = str(tmp_path / "2026-10-14.tar")
  jobs = _append_jobs(256)
  for _ in range(jq.job_max_attempts() - 1):
    assert (
      qo._fail_append_batch(
        store,
        tar,
        jobs,
        reason="append_failed:false",
        archive_data_dir=str(tmp_path),
        log_fn=lambda *a, **k: None,
      )
      == "requeued"
    )
  assert jq._dead_letter_index(str(tmp_path)) == {}
  for job in jobs[:3]:
    assert jq.read_job_attempt(store, kind="append", identity=job.identity) == 0


def test_append_dead_letter_reason_carries_outcome(tmp_path):
  """DE-18: an exhausted batch dead-letters with the outcome in the reason."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  store = SyncTimedbJobStore("")
  tar = str(tmp_path / "2026-10-18.tar")
  jobs = _append_jobs(3, day=(2026, 10, 18))
  out = ""
  for _ in range(jq.job_max_attempts()):
    out = qo._fail_append_batch(
      store,
      tar,
      jobs,
      reason="append_failed:CalledProcessError",
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
    )
  assert out == "dead_letter"
  index = jq._dead_letter_index(str(tmp_path))
  assert {e["reason"] for e in index.values()} == {
    "append_failed:CalledProcessError"
  }
  assert all(not e["terminal"] for e in index.values()), "cooldown, not final"


def test_release_steps_run_before_consume_steps(tmp_path, monkeypatch):
  """A16: with a sealed copy, raw delete (release) runs before the reseal."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  (daily / "2020-01-11.tar").write_bytes(b"tar")
  (daily / "2020-01-11.tar.zst").write_bytes(b"zst")
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    qo, "_day_close_append_or_hot_active", lambda *a, **k: False
  )
  order = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
    lambda *a, **k: order.append("seal"),
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def phase(self, _t):
      return "verification_complete"

    def run_pre_seal_verify_sync(self, _t):
      return True

    def run_post_seal_verify_sync(self, _t):
      return False

    def apply_batch_delete(self, _t):
      order.append("release_delete")
      return 0

    def has_closed_raw_on_disk(self, _t):
      return True

    def remaining_raw_paths_blocking_tar_drop(self, _t):
      return {"z": ["/raw/x"]}

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    _Coord,
  )
  monkeypatch.setattr(
    qo,
    "reconcile_open_tar_with_sealed_zst",
    lambda *a, **k: None,
    raising=False,
  )
  qo._run_day_close_job(
    "2020-01-11",
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
    log_fn=lambda *a, **k: None,
  )
  # The remaining raw is not a tar member, so the seal is deferred; it must
  # never run before the release step.
  assert "release_delete" in order
  assert "seal" not in order[: order.index("release_delete")]


def test_reconcile_failure_counts_toward_noprogress(tmp_path, monkeypatch):
  """DE-28: a deterministic reconcile failure escalates instead of looping."""
  from hpcperfstats.dbload.lib import (
    conf_parser as cfg,
    sync_timedb_queue_orchestrator as qo,
  )

  monkeypatch.setattr(cfg, "get_sync_noprogress_escalate_after", lambda: 3)
  daily = tmp_path / "daily"
  daily.mkdir()
  (daily / "2020-01-12.tar").write_bytes(b"tar")
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    qo, "_day_close_append_or_hot_active", lambda *a, **k: False
  )

  def _boom(*_a, **_k):
    raise RuntimeError("deterministic merge bug")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.reconcile_open_tar_with_sealed_zst",
    _boom,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
    lambda *a, **k: None,
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def phase(self, _t):
      return "verification_complete"

    def run_pre_seal_verify_sync(self, _t):
      return True

    def run_post_seal_verify_sync(self, _t):
      return False

    def apply_batch_delete(self, _t):
      return 0

    def has_closed_raw_on_disk(self, _t):
      return False

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    _Coord,
  )
  outs = [
    qo._run_day_close_job(
      "2020-01-12",
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
    )
    for _ in range(3)
  ]
  assert outs[:2] != ["error", "error"] and outs[2] == "error"


def test_dead_worker_inflight_reaped_when_recycle_disabled(monkeypatch):
  """DE-15: a terminated pool's pending future is requeued, not held forever."""
  import threading

  from hpcperfstats.dbload.lib import (
    conf_parser as cfg,
    sync_timedb_queue_orchestrator as qo,
  )
  from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    SyncTimedbThreadPool,
  )

  monkeypatch.setattr(
    cfg, "get_sync_ingest_stuck_inflight_recycle_s", lambda: 0
  )
  gate = threading.Event()
  started = threading.Event()
  pool = SyncTimedbThreadPool(max_workers=1, thread_role="ingest-pool")
  running = pool.apply_async(lambda: started.set() or gate.wait())
  started.wait(5)
  pool.terminate()  # abandons the running task; its future stays pending
  requeued = []
  monkeypatch.setattr(
    qo,
    "_requeue_claimed_job_without_attempt_bump",
    lambda client, claim, log_fn=None: requeued.append(claim),
  )
  inflight = {"/raw/h/1": running}
  claims = {"/raw/h/1": "claim-1"}
  try:
    n = qo._reap_dead_owner_ingest_inflight(
      object(), inflight=inflight, claims=claims, log_fn=lambda *a, **k: None
    )
  finally:
    gate.set()
  assert n == 1 and requeued == ["claim-1"] and inflight == {}
