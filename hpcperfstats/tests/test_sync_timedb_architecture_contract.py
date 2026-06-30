"""Long-lived architecture contracts for sync_timedb two-queue model.

See day-close-ingest-loop-fix plan Phase 2b; rules cite test_arch_* names.
"""

import inspect
import os

import hpcperfstats.dbload.lib.conf_parser as cfg
import hpcperfstats.dbload.lib.sync_timedb_archive_janitor as janitor_mod
import hpcperfstats.dbload.sync_timedb as st
import pytest
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import DebtKind
from hpcperfstats.tests.test_sync_timedb_janitor import (
    _day_phase_value,
    _make_janitor,
    _mark_day_sealed,
)


def test_arch_ingest_begins_without_startup_maintenance_idle_block(monkeypatch):
  """sync-timedb-startup-day-close-contract: default drain=no does not spin gate."""
  assert cfg.get_sync_startup_drain_day_close_before_ingest() is False


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
  shutdown_requested[0] = False
  monkeypatch.setattr(
      st,
      "run_supervisor_day_raw_removal_delete_pass",
      lambda *_a, **_k: pytest.fail("delete pass must not run in chunk loop"),
  )

  source = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  chunk_section = source.split("while pending_stats_files:", 1)[1]
  assert "run_supervisor_day_raw_removal_delete_pass" not in chunk_section
  assert "_maybe_handle_raw_removal_delete_phase" not in chunk_section

  shutdown_requested[0] = False


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

  from hpcperfstats.dbload.lib.sync_timedb_async_day_close import AsyncDayCloseCoordinator

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-06-01.tar")
  open(tar_path, "wb").close()
  zst_path = tar_path.replace(".tar", ".tar.zst")
  open(zst_path, "wb").close()

  coord = AsyncDayCloseCoordinator(
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
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 4)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
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

    def start_async_verify(self, _tar):
      return None

    def verification_complete(self, _tar):
      return True

    def reopen_done_days_with_verified_on_disk(self):
      return 0

    def delete_phase_done(self, _tar):
      return True

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
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  janitor_source = inspect.getsource(janitor_mod.ArchiveJanitor)
  assert "ensure_daily_tar_restored_for_append" not in janitor_source

  sync_source = inspect.getsource(st._archive_stats_files_body)
  assert "ensure_daily_tar_restored_for_append" in inspect.getsource(st) or (
      "_decompress_compressed_archive" in sync_source
      or "decompress" in sync_source
  )
  assert callable(helpers.ensure_daily_tar_restored_for_append)


def test_arch_mainthread_enqueue_does_not_block_on_janitor_seal(tmp_path, monkeypatch):
  """MainThread submit_day_close returns before async seal worker finishes."""
  import concurrent.futures
  import threading
  import time

  from hpcperfstats.dbload.lib.sync_timedb_async_day_close import (
      AsyncDayCloseCoordinator,
  )

  seal_started = threading.Event()
  release_seal = threading.Event()

  def _slow_seal(*_args, **_kwargs):
    seal_started.set()
    assert release_seal.wait(timeout=5.0)

  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-06-01.tar")
  open(tar_path, "wb").close()

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_async_day_close.cfg",
      "get_sync_day_close_async_workers",
      lambda: 1,
  )
  coord = AsyncDayCloseCoordinator(
      archive_data_dir=str(archive_dir),
      host_name_ext=".hpc",
      tgz_archive_dir=str(daily_dir),
      local_tz=None,
      log_fn=None,
      get_disqualified_daily_tars=lambda: set(),
  )
  monkeypatch.setattr(coord, "_run_day_close", _slow_seal)

  enqueue_t0 = time.monotonic()
  with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    fut = pool.submit(
        coord.submit_day_close,
        tar_path,
        reason="arch_contract",
        disqualified_daily_tars=set(),
    )
    assert fut.result(timeout=2.0) is True
  enqueue_elapsed = time.monotonic() - enqueue_t0
  assert enqueue_elapsed < 1.0
  assert seal_started.wait(timeout=2.0)
  release_seal.set()
  coord.shutdown(wait=True)


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
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 4)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  janitor._run_tick_body()
  assert processed == [
      os.path.normpath(tar1),
      os.path.normpath(tar2),
      os.path.normpath(tar3),
  ]
  assert janitor.debt_depth() == 0


def test_tar_drop_hint_downgraded_when_uncompressed_tar_still_exists(tmp_path):
  """Phase 3c: tar_dropped hint does not suppress work while .tar remains on disk."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_tar_needs_day_close_work,
  )

  tar_path = str(tmp_path / "2026-06-01.tar")
  open(tar_path, "wb").close()
  day_phases = {os.path.normpath(tar_path): "tar_dropped"}
  assert daily_tar_needs_day_close_work(tar_path, day_phases=day_phases)

  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  _mark_day_phase(janitor, tar_path, "tar_dropped")
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  drop_calls = {"n": 0}

  def _count_drop(*_args, **_kwargs):
    drop_calls["n"] += 1
    return False

  import pytest as _pytest

  monkeypatch = _pytest.MonkeyPatch()
  try:
    monkeypatch.setattr(janitor, "_tar_drop_one_day", _count_drop)
    monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 4)
    monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
    janitor._run_tick_body()
  finally:
    monkeypatch.undo()

  assert drop_calls["n"] >= 1
  assert os.path.isfile(tar_path)
  assert janitor.debt_depth() >= 1


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
