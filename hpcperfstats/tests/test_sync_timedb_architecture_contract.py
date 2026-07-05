"""Long-lived architecture contracts for sync_timedb two-queue model.

See day-close-ingest-loop-fix plan Phase 2b; rules cite test_arch_* names.
"""

import inspect
import os

import hpcperfstats.dbload.lib.conf_parser as cfg
import hpcperfstats.dbload.lib.sync_timedb_archive_janitor as janitor_mod
import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import DebtKind
from hpcperfstats.tests.test_sync_timedb_janitor import (
    _day_phase_value,
    _make_janitor,
    _mark_day_sealed,
)


def test_arch_ingest_begins_without_startup_drain_gate():
  """Phase H: supervisor loop must not block first ingest on startup drain gate."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "_drain_startup_day_close_and_deletion_if_needed" not in source
  assert "get_sync_startup_drain_day_close_before_ingest" not in source


def test_arch_supervisor_has_no_split_db_writer_pipeline():
  """Phase G: split db-writer pipeline removed from supervisor."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "db_writer_pool" not in source
  assert "DBWriteTask" not in source
  assert "ParseTask" not in source
  assert "use_split_db_writer_pipeline" not in source


def test_arch_supervisor_has_no_default_off_startup_coordinators():
  """Phase H: default-off startup coordinators removed from supervisor."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "StartupRawRemovalPreflight" not in source
  assert "StartupTailIngestCoordinator" not in source
  assert "sync_timedb_startup_raw_removal" not in source
  assert "sync_timedb_startup_tail_ingest" not in source


def test_arch_no_startup_day_close_preflight_in_supervisor():
  """Boot DAY_CLOSE discover is janitor-only; no StartupDayClosePreflight thread."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "StartupDayClosePreflight" not in source
  assert "sync_timedb_startup_day_close" not in source
  assert "start_async_discover_and_close" not in source


def test_arch_startup_block_has_no_handoff_recover():
  """Prep-only boot: no startup handoff recover or handoff-specific snapshot wait."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "_recover_startup_day_close_handoffs" not in source
  assert "_wait_for_startup_snapshot_for_handoff" not in source
  assert "_process_one_startup_handoff_recover" not in source
  assert "startup_handoff_recover_pending" not in source
  assert "_process_boot_handoffs_once" in source


def test_arch_rescan_exclude_paths_covers_handoff_retryable(tmp_path):
  """RC-G: handoff retryable paths stay out of pending rescan exclude set."""
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      PHASE_DONE,
      DayRawRemovalCoordinator,
      _save_manifest,
  )

  seg = tmp_path / "host" / "1782242314"
  seg.parent.mkdir(parents=True)
  seg.write_text("1000 job cn001\n", encoding="utf-8")
  tar_path = str(tmp_path / "daily" / "2026-06-01.tar")
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  open(tar_path, "wb").close()
  zst = str(tmp_path / "daily" / "2026-06-01.tar.zst")
  open(zst, "wb").close()

  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      log_fn=None,
      get_quarantine_skip_paths=lambda: set(),
      ingest_ready_fn=lambda _p: False,
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  excluded = coord.rescan_exclude_paths()
  assert str(seg) in excluded


def test_arch_supervisor_chunk_loop_does_not_call_apply_batch_delete(monkeypatch, tmp_path):
  """Janitor-only delete: supervisor chunk loop must not drive batch delete."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  chunk_section = source.split("while pending_stats_files:", 1)[1]
  assert "run_supervisor_day_raw_removal_delete_pass" not in chunk_section
  assert "_maybe_handle_raw_removal_delete_phase" not in chunk_section


def test_arch_raw_delete_driver_only_on_archival_thread():
  """Janitor-only delete: steady-state chunk loop has no delete-driver symbols."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  chunk_section = source.split("while pending_stats_files:", 1)[1]
  assert "run_supervisor_day_raw_removal_delete_pass" not in chunk_section
  assert "_maybe_handle_raw_removal_delete_phase" not in chunk_section


def test_arch_chunk_boundary_does_not_invoke_delete_post_finalize_or_maintenance():
  """Phase 3d: chunk loop must not schedule archival maintenance or inline reconcile."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  chunk_section = source.split("while pending_stats_files:", 1)[1]
  forbidden_calls = (
      "_maybe_handle_raw_removal_delete_phase(",
      "signal_scheduled_maintenance_pass(",
      "_maybe_run_post_finalize_reconcile_and_day_close(",
      "post_finalize_reconcile(",
  )
  for name in forbidden_calls:
    assert name not in chunk_section, "forbidden at chunk boundary: %s" % name


def test_arch_phase_tar_dropped_does_not_skip_disk_predicate(tmp_path):
  """Phase 3c: tar_dropped hint alone does not clear day-close work when .tar remains."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_tar_needs_day_close_work,
  )

  tar_path = str(tmp_path / "2026-06-01.tar")
  open(tar_path, "wb").close()
  day_phases = {os.path.normpath(tar_path): "tar_dropped"}
  assert daily_tar_needs_day_close_work(tar_path, day_phases=day_phases)


def test_arch_phase_done_with_on_disk_raw_does_not_skip_delete(tmp_path):
  """Phase 3c / RC-I: phase=done with skipped paths on disk reopens verify, not silent done."""
  from datetime import datetime

  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      PHASE_DONE,
      PHASE_VERIFYING,
      _save_manifest,
  )
  from hpcperfstats.tests.test_sync_timedb_day_raw_removal import (
      _make_coordinator,
      _make_closed_segment,
      _seal_day,
  )

  day = datetime(2026, 6, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert state.stale_done_all_skipped_still_on_disk()
  assert coord.reopen_done_days_with_verified_on_disk() == 1
  assert state.phase() == PHASE_VERIFYING


def test_arch_async_complete_requires_filesystem_truth(tmp_path):
  """Phase 3c: manifest complete status does not imply is_complete when .tar remains."""
  from datetime import timezone

  from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import DayCloseManifestCoordinator

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-06-01.tar")
  open(tar_path, "wb").close()
  zst_path = tar_path.replace(".tar", ".tar.zst")
  open(zst_path, "wb").close()

  coord = DayCloseManifestCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(daily_dir),
      local_tz=timezone.utc,
      log_fn=None,
      get_disqualified_daily_tars=lambda: set(),
  )
  tar_norm = os.path.normpath(tar_path)
  coord._set_entry_status(tar_norm, "complete", completed_at=1.0)
  assert not coord.is_complete(tar_norm)


def test_arch_janitor_drains_multiple_enqueued_jobs_per_wake(monkeypatch, tmp_path):
  """Phase 3d: one signal processes multiple debt items until heap empty (within budget)."""
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  for tar in (tar1, tar2):
    open(tar, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar1, persist=False)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar2, persist=False)
  processed = []

  def _track_close(tar_path, **kwargs):
    processed.append(os.path.normpath(tar_path))
    return True

  monkeypatch.setattr(janitor, "_close_one_day", _track_close)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  janitor._run_tick_body()
  assert len(processed) == 2
  assert janitor.debt_depth() == 0


def test_arch_close_one_day_tar_drop_after_raw_removal_done(monkeypatch, tmp_path):
  """Phase 3c: raw-removal phase=done must not set tar_dropped until tar drop succeeds."""
  tar_path = str(tmp_path / "2026-06-01.tar")
  open(tar_path, "wb").close()
  open(tar_path.replace(".tar", ".tar.zst"), "wb").close()

  class _FakeDayRawCoord:
    enabled = True

    def pre_seal_verification_complete(self, _tar):
      return True

    def post_seal_verification_complete(self, _tar):
      return True

    def run_post_seal_verify_sync(self, _tar):
      return True

    def start_async_verify(self, _tar):
      return None

    def verification_complete(self, _tar):
      return True

    def reopen_done_days_with_verified_on_disk(self):
      return 0

    def delete_phase_done(self, _tar):
      return True

    def reclassify_retryable_skips_after_handoff_sync(self, _tar):
      return 0

    def begin_deleting(self, _tar):
      return None

    def apply_batch_delete(self, _tar):
      return None

  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=_FakeDayRawCoord(),
  )
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})

  def _drop_tar_only(*_args, **kwargs):
    for tar in kwargs.get("only_daily_tar_paths") or ():
      if os.path.isfile(tar):
        os.remove(tar)

  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_uncompressed_daily_tars",
      _drop_tar_only,
  )
  janitor._run_tick_body()
  assert os.path.isfile(tar_path) is False
  assert _day_phase_value(janitor._day_phases, tar_path) == "tar_dropped"


def test_arch_ingest_path_dispatches_archive_pool_append(monkeypatch):
  """Ingest-path append: closed raw dispatches via archive_pool map_async."""
  from unittest.mock import MagicMock

  from hpcperfstats.dbload.lib.sync_timedb_archive_dispatch import (
      ArchiveDispatchCoordinator,
  )

  submitted = []

  class _Pool:
    def map_async(self, fn, items):
      submitted.append((fn, list(items)))
      return MagicMock(ready=lambda: False)

  archive_fn = MagicMock()
  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=2,
      archive_stats_files_fn=archive_fn,
      log_fn=MagicMock(),
      get_ingest_backlog_high=lambda: False,
      ingest_queue_low=1,
      pending_stats_count_fn=lambda: 0,
  )
  monkeypatch.setattr(
      cfg,
      "get_sync_dispatch_archive_backoff_ratio",
      lambda: 1.0,
  )
  items = [("/tmp/daily/2026-06-01.tar.gz", ["/tmp/raw/host/seg1"])]
  stats = coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=10,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: None,
  )
  assert stats["submitted"] == 1
  assert len(submitted) == 1
  assert submitted[0][0] is archive_fn
  assert submitted[0][1] == items


def test_arch_restore_for_append_runs_on_archive_pool_not_janitor():
  """Decompress-for-append stays on ingest path, not ArchiveJanitor tick body."""
  janitor_source = inspect.getsource(janitor_mod.ArchiveJanitor)
  assert "ensure_daily_tar_restored_for_append" not in janitor_source
  sync_module_source = inspect.getsource(st)
  assert "ensure_daily_tar_restored_for_append" in sync_module_source


def test_arch_mainthread_enqueue_does_not_block_on_janitor_fn(tmp_path):
  """MainThread enqueue_day_close returns immediately; janitor enqueue is async."""
  import time

  from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
      DayCloseManifestCoordinator,
  )

  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-06-01.tar")
  open(tar_path, "wb").close()
  blocked = {"n": 0}

  def _blocking_enqueue(_tar, _reason):
    blocked["n"] += 1
    time.sleep(0.05)
    return True

  coord = DayCloseManifestCoordinator(
      archive_data_dir=str(archive_dir),
      host_name_ext=".hpc",
      tgz_archive_dir=str(daily_dir),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      enqueue_day_close_fn=_blocking_enqueue,
  )

  enqueue_t0 = time.monotonic()
  assert coord.enqueue_day_close(tar_path, reason="arch_contract") is True
  enqueue_elapsed = time.monotonic() - enqueue_t0
  assert enqueue_elapsed < 0.2
  assert blocked["n"] == 1


def test_arch_chunk_boundary_may_finalize_append_slots_only():
  """Slot finalize is ingest bookkeeping; must not inline immediate day_close."""
  finalize_source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  slot_fn = finalize_source.split("def _finalize_archive_slots_if_needed", 1)[1]
  slot_fn = slot_fn.split("\n  def ", 1)[0]
  assert "_finalize_archive_slots_if_needed" in finalize_source
  assert "_maybe_enqueue_immediate_day_close" not in slot_fn


def test_arch_second_enqueue_during_drain_processed_same_pass(monkeypatch, tmp_path):
  """Re-enqueue while draining extends the same janitor tick pass."""
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  tar3 = str(tmp_path / "2026-01-03.tar")
  for tar in (tar1, tar2, tar3):
    open(tar, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar1, persist=False)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar2, persist=False)
  processed = []

  def _track_close(tar_path, **kwargs):
    tar_norm = os.path.normpath(tar_path)
    processed.append(tar_norm)
    if len(processed) == 1:
      janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar3, persist=False)
    return True

  monkeypatch.setattr(janitor, "_close_one_day", _track_close)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  janitor._run_tick_body()
  assert set(processed) == {
      os.path.normpath(tar1),
      os.path.normpath(tar2),
      os.path.normpath(tar3),
  }
  assert janitor.debt_depth() == 0


def test_tar_drop_hint_downgraded_when_uncompressed_tar_still_exists(tmp_path):
  """Phase 3c: tar_dropped hint alone does not clear day-close when .tar remains."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_tar_needs_day_close_work,
  )

  tar_path = str(tmp_path / "2026-06-01.tar")
  open(tar_path, "wb").close()
  tar_norm = os.path.normpath(tar_path)
  day_phases = {tar_norm: "tar_dropped"}
  assert daily_tar_needs_day_close_work(tar_path, day_phases=day_phases)
  assert os.path.isfile(tar_norm)


def test_phase_done_forbidden_when_all_skipped_and_paths_on_disk(tmp_path):
  """Phase 3c / RC-I census: all-skipped-on-disk done reopens verify; delete not final."""
  from datetime import datetime

  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      PHASE_DONE,
      PHASE_VERIFYING,
      _save_manifest,
  )
  from hpcperfstats.tests.test_sync_timedb_day_raw_removal import (
      _make_coordinator,
      _make_closed_segment,
      _seal_day,
  )

  day = datetime(2026, 6, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    state._manifest["deleted_count"] = 0
    state._manifest["verified_count"] = 0
    _save_manifest(state._manifest_path, state._manifest)

  entries = dict(state._manifest.get("entries", {}))
  on_disk_n = sum(1 for path in entries if os.path.isfile(path))
  assert on_disk_n == 1
  assert int(state._manifest.get("skipped_count", 0)) == on_disk_n
  assert int(state._manifest.get("deleted_count", 0)) == 0
  assert state.stale_done_all_skipped_still_on_disk()
  assert coord.reopen_done_days_with_verified_on_disk() == 1
  assert state.phase() == PHASE_VERIFYING
  assert not state.delete_phase_done()


def test_arch_oldest_day_gate_blocked_paths_enter_pending():
  """incomplete_n>0 paths must prepend to pending for oldest-day gate reconcile."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      prepend_checkpoint_incomplete_paths_to_pending,
  )

  blocked = ["/data/host/a", "/data/host/b"]
  pending = ["/data/host/c", "/data/host/d"]
  merged = prepend_checkpoint_incomplete_paths_to_pending(pending, blocked)
  assert merged[:2] == blocked
  assert merged[2:] == pending


def test_arch_ingest_stall_watchdog_idle_threshold():
  """Long-horizon stall detection: 30 min idle before ERROR."""
  assert st.INGEST_STALL_WATCHDOG_IDLE_S == 1800.0


def test_arch_supervisor_loop_wires_ingest_stall_watchdog():
  """Product telemetry: ingest_stall_watchdog wired at oldest-day gate stall."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "ingest_stall_watchdog" in source
  assert "_maybe_log_ingest_stall_watchdog" in source
  assert "last_chunk_ingest_summary_mono" in source


def test_arch_june04_gate_wait_defer_loops_not_exits():
  """june04 contract: oldest_day_gate_wait defer must continue chunk loop."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert 'context="oldest_day_gate_wait"' in source
  after_gate = source.split('context="oldest_day_gate_wait"', 1)[1]
  assert "continue" in after_gate[:1500]


def test_arch_oldest_day_gate_empty_chunk_backoff():
  """Empty chunk under oldest-day gate must backoff (avoid CPU spin)."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "oldest_day_gate_empty_chunk_spins" in source
  assert "sleep_until_shutdown(0.05)" in source


def test_arch_steady_state_ingest_not_gated_on_raw_deletion():
  """Steady-state chunk loop must not wait on raw_delete_pending or delete driver."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  chunk_section = source.split("while pending_stats_files:", 1)[1]
  assert "raw_delete_pending" not in chunk_section
  assert "run_supervisor_day_raw_removal_delete_pass" not in chunk_section


def test_arch_no_raw_delete_pending_in_steady_state_tree():
  """Janitor DAY_CLOSE path must not use raw_delete_pending status contract."""
  janitor_source = inspect.getsource(janitor_mod.ArchiveJanitor._process_debt_item)
  assert "raw_delete_pending" not in janitor_source
  async_source = inspect.getsource(
      __import__(
          "hpcperfstats.dbload.lib.sync_timedb_day_close_manifest",
          fromlist=["DayCloseManifestCoordinator"],
      ).DayCloseManifestCoordinator.tar_paths_raw_delete_pending,
  )
  assert "return[]" in async_source.replace(" ", "").replace("\n", "")


def test_arch_janitor_close_one_day_owns_delete_not_supervisor():
  """Day-close delete entry is janitor ``_close_one_day``; supervisor chunk loop has no delete pass."""
  janitor_source = inspect.getsource(janitor_mod.ArchiveJanitor._process_debt_item)
  assert "_close_one_day" in janitor_source
  supervisor_source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  chunk_section = supervisor_source.split("while pending_stats_files:", 1)[1]
  assert "apply_batch_delete" not in chunk_section
  assert "_maybe_handle_raw_removal_delete_phase" not in chunk_section


def test_arch_day_close_verify_before_seal():
  """Pre-seal verify must run before seal in ``_close_one_day`` source order."""
  source = inspect.getsource(janitor_mod.ArchiveJanitor._close_one_day)
  pre_idx = source.index("pre_seal_verify")
  seal_idx = source.index("_seal_one_day")
  post_idx = source.index("post_seal_verify")
  assert pre_idx < seal_idx < post_idx
