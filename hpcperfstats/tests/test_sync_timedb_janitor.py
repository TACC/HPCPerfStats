"""Unit tests for ArchiveJanitor debt queue and micro-batch ticks."""

import os
import threading
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

import hpcperfstats.conf_parser as cfg
import hpcperfstats.dbload.sync_timedb_archive_janitor as janitor_mod
from hpcperfstats.dbload.sync_timedb_archive_janitor import (
    ArchiveJanitor,
    DayDebt,
    DebtKind,
    _LOCK_CLEANUP_TAR_SENTINEL,
    _debt_sort_key,
)


def _day_phase_value(day_phases, tar_path):
  value = day_phases.get(os.path.normpath(tar_path))
  if isinstance(value, dict):
    return value.get("phase")
  return value


def _zst_path_for_tar(tar_path):
  from hpcperfstats.dbload.archive_compress import compressed_sibling_paths

  zst_path, _gz_path = compressed_sibling_paths(tar_path)
  return zst_path


def _mark_day_phase(janitor, tar_path, phase):
  from hpcperfstats.dbload.sync_timedb_archive_maint import day_phase_hint_entry

  tar_norm = os.path.normpath(tar_path)
  janitor._day_phases[tar_norm] = day_phase_hint_entry(tar_norm, phase)


def _mark_day_sealed(janitor, tar_path):
  _mark_day_phase(janitor, tar_path, "sealed")


def _mark_day_raw_removed(janitor, tar_path):
  _mark_day_phase(janitor, tar_path, "raw_removed")


def _make_janitor(**kwargs):
  defaults = {
      "archive_data_dir": "/tmp/archive",
      "host_name_ext": ".hpc",
      "tgz_archive_dir": "/tmp/daily",
      "local_tz": timezone.utc,
      "log_fn": MagicMock(),
      "get_disqualified_daily_tars": lambda: set(),
      "get_ingest_backlog_high": lambda: False,
      "get_pending_stats_count": lambda: 0,
      "get_idle_seconds": lambda: 0.0,
      "ingest_ready_fn": None,
      "archive_stats_files_fn": None,
  }
  defaults.update(kwargs)
  janitor = ArchiveJanitor(**defaults)
  janitor._persist_hints = MagicMock()
  return janitor


def test_enqueue_debt_dedupes_same_kind_and_tar():
  janitor = _make_janitor()
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  assert janitor.debt_depth() == 1


def test_debt_queue_payload_serializes_heap_entries():
  janitor = _make_janitor()
  janitor._enqueue_debt(DebtKind.TAR_DROP, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  payload = janitor._debt_queue_payload()
  kinds = {entry["kind"] for entry in payload}
  assert kinds == {DebtKind.DAY_CLOSE.value}
  assert len(payload) == 2


def test_accrue_debt_full_enqueues_raw_and_tar_for_remaining_raw(monkeypatch):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  janitor = _make_janitor()

  def snapshot(*_a, **_k):
    return ArchiveMaintenanceSnapshot(
        closed_paths=[],
        remaining_raw_by_gz={"/tmp/2026-03-01.tar.gz": ["/tmp/raw-a"]},
        mapping={},
        ready_paths=set(),
    )

  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", snapshot)
  monkeypatch.setattr(janitor_mod, "collect_days_with_unmapped_closed_raw", lambda *_a, **_k: set())
  janitor._accrue_debt_full(reason="test")
  kinds = {debt.kind for debt in janitor._debt_heap}
  assert DebtKind.DAY_CLOSE in kinds


def test_tick_raw_remove_uses_allow_auto_seal_false(monkeypatch):
  captured = {}
  tar_path = "/tmp/2026-01-01.tar"
  janitor = _make_janitor()
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, tar_path, persist=False)

  def fake_remove(*_a, **kwargs):
    captured.update(kwargs)

  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", fake_remove)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  janitor._run_tick_body()
  assert captured.get("allow_auto_seal") is False


def test_janitor_tick_budget_interrupt_requeues_remaining_debt(monkeypatch, tmp_path):
  zst = tmp_path / "2026-01-01.tar.zst"
  zst.write_bytes(b"zst")
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  for kind, tar in (
      (DebtKind.SEAL_PRIOR_DAY, str(tmp_path / "2026-01-01.tar")),
      (DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar"),
      (DebtKind.TAR_DROP, "/tmp/2026-01-03.tar"),
  ):
    janitor._enqueue_debt(kind, tar, persist=False)
  times = iter([100.0, 100.0, 200.0])
  monkeypatch.setattr(janitor_mod.time, "time", lambda: next(times))
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 50.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 3)
  janitor._run_tick_body()
  assert janitor.debt_depth() == 2
  assert janitor.stats()["janitor_budget_throttled"] >= 1


def test_janitor_tick_exception_requeues_unprocessed_debt(monkeypatch):
  janitor = _make_janitor()
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar", persist=False)

  def boom(*_a, **_k):
    raise RuntimeError("seal failed")

  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", boom)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  janitor._run_tick_body()
  assert janitor.debt_depth() >= 1
  assert DebtKind.DAY_CLOSE in {d.kind for d in janitor._debt_heap}


def test_janitor_tar_drop_blocks_when_accrual_snapshot_none(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._accrual_snapshot = None
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {str(tmp_path / "2026-01-01.tar.zst"): ["/tmp/raw"]},
  )
  called = {"drop": False}

  def fake_drop(*_a, **_k):
    called["drop"] = True

  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", fake_drop)
  janitor._run_tick_body()
  assert os.path.isfile(tar_path)
  assert called["drop"] is False


def test_janitor_tar_drop_blocks_when_raw_appears_after_accrual(monkeypatch, tmp_path):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._accrual_snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
  )
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {str(tmp_path / "2026-01-01.tar.zst"): ["/tmp/new-raw"]},
  )
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._run_tick_body()
  assert os.path.isfile(tar_path)


def test_janitor_concurrent_accrual_and_tick_preserves_debt_heap(monkeypatch):
  janitor = _make_janitor()
  barrier = threading.Barrier(2)

  def accrue():
    barrier.wait()
    janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-10.tar", persist=False)

  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-01.tar", persist=False)
  t = threading.Thread(target=accrue)
  t.start()
  barrier.wait()
  janitor._run_tick_body()
  t.join()
  with janitor._debt_lock:
    for debt in janitor._debt_heap:
      assert (debt.kind.value, debt.tar_path) in janitor._debt_seen


def test_janitor_seal_prior_day_defers_when_closed_raw_remains(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, tar_path, persist=False)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {str(tmp_path / "2026-01-01.tar.zst"): ["/tmp/raw"]},
  )
  called = {"seal": 0}
  monkeypatch.setattr(
      janitor_mod,
      "atomic_seal_tar_to_zst",
      lambda *a, **k: called.__setitem__("seal", called["seal"] + 1),
  )
  janitor._run_tick_body()
  assert called["seal"] == 0
  assert janitor.debt_depth() >= 1


def test_janitor_day_phases_set_only_on_verified_success(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, tar_path, persist=False)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {str(tmp_path / "2026-01-01.tar.zst"): ["/tmp/raw"]},
  )
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  janitor._run_tick_body()
  assert _day_phase_value(janitor._day_phases, tar_path) != "raw_removed"


def test_janitor_debt_max_entries_logs_and_caps_heap(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 2)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-03.tar", persist=False)
  assert janitor.debt_depth() == 2


def test_janitor_persisted_debt_queue_matches_heap_after_cap(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 2)
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.TAR_DROP, "/tmp/2026-01-03.tar", persist=False)
  payload_keys = {(e["kind"], e["tar_path"]) for e in janitor._debt_queue_payload()}
  heap_keys = {(d.kind.value, d.tar_path) for d in janitor._debt_heap}
  assert payload_keys == heap_keys


def test_janitor_interval_accrue_enqueues_lock_cleanup_debt(monkeypatch):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  janitor = _make_janitor()
  janitor._last_accrual_at = 0.0
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: ArchiveMaintenanceSnapshot(
          closed_paths=[], remaining_raw_by_gz={}, mapping={}, ready_paths=set(),
      ),
  )
  monkeypatch.setattr(janitor_mod, "collect_days_with_unmapped_closed_raw", lambda *_a, **_k: set())
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda *_a, **_k: [])
  assert janitor.maybe_accrue_debt_if_due(1.0)
  kinds = {d.kind for d in janitor._debt_heap}
  assert DebtKind.LOCK_CLEANUP in kinds


def test_janitor_interval_accrue_enqueues_dedupe_for_duplicate_tar(monkeypatch, tmp_path):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._last_accrual_at = 0.0
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: ArchiveMaintenanceSnapshot(
          closed_paths=[], remaining_raw_by_gz={}, mapping={}, ready_paths=set(),
      ),
  )
  monkeypatch.setattr(janitor_mod, "collect_days_with_unmapped_closed_raw", lambda *_a, **_k: set())
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda d: [tar_path])
  monkeypatch.setattr(janitor_mod, "tar_has_duplicate_file_members", lambda p: p == tar_path)
  assert janitor.maybe_accrue_debt_if_due(1.0)
  assert any(d.kind == DebtKind.DEDUPE and d.tar_path == tar_path for d in janitor._debt_heap)


def test_janitor_rss_defer_reschedules_tick(monkeypatch):
  janitor = _make_janitor()
  submitted = []

  class _Exec:
    def submit(self, fn, *args, **kwargs):
      submitted.append(fn)
      fn(*args, **kwargs)

      class _F:
        def done(self):
          return True

      return _F()

    def shutdown(self, wait=True):
      del wait

  janitor._executor = _Exec()
  monkeypatch.setattr(janitor_mod, "read_process_rss_bytes", lambda: 999999999)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_supervisor_rss_limit_mb", lambda: 1)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  janitor.signal_work_available()
  assert len(submitted) >= 2


def test_janitor_tar_drop_runs_same_tick_after_raw_remove_when_fresh_probe(
    monkeypatch, tmp_path,
):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)

  def remaining_seq(*_a, **_k):
    if not hasattr(remaining_seq, "n"):
      remaining_seq.n = 0
    remaining_seq.n += 1
    if remaining_seq.n == 1:
      return {str(tmp_path / "2026-01-01.tar.zst"): ["/tmp/raw"]}
    return {}

  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", remaining_seq)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)

  def drop_tar(*_a, **_k):
    if os.path.isfile(tar_path):
      os.remove(tar_path)

  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", drop_tar)
  janitor._run_tick_body()
  assert janitor._day_phases.get(tar_path) == "tar_dropped"


def test_janitor_effective_tick_limits_burst_at_watermark(monkeypatch):
  janitor = _make_janitor()
  for i in range(55):
    janitor._enqueue_debt(DebtKind.RAW_REMOVE, f"/tmp/2026-{i:02d}-01.tar", persist=False)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_high_watermark", lambda: 50)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_burst_factor", lambda: 2.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 30.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 2)
  budget, max_days = janitor._effective_tick_limits()
  assert budget >= 60.0
  assert max_days >= 4


def test_janitor_load_hints_restores_debt_on_init(monkeypatch, tmp_path):
  from hpcperfstats.dbload.sync_timedb_archive_maint import save_archive_maint_hints

  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  save_archive_maint_hints(
      archive_dir,
      host_dirs={},
      paths={},
      validated_days={},
      debt_queue=[
          {"kind": DebtKind.RAW_REMOVE.value, "tar_path": "/tmp/2026-01-01.tar"},
          {"kind": DebtKind.TAR_DROP.value, "tar_path": "/tmp/2026-01-02.tar"},
      ],
  )
  janitor = _make_janitor(archive_data_dir=archive_dir)
  assert janitor.debt_depth() == 2
  assert {d.kind for d in janitor._debt_heap} == {DebtKind.DAY_CLOSE}


def test_janitor_accrue_full_skips_unmapped_closed_raw_days(monkeypatch):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  janitor = _make_janitor()
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: ArchiveMaintenanceSnapshot(
          closed_paths=["/tmp/unmapped"],
          remaining_raw_by_gz={"/tmp/2026-03-01.tar.gz": ["/tmp/unmapped"]},
          mapping={},
          ready_paths=set(),
      ),
  )
  monkeypatch.setattr(
      janitor_mod,
      "collect_days_with_unmapped_closed_raw",
      lambda *_a, **_k: {"/tmp/2026-03-01.tar"},
  )
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda *_a, **_k: [])
  janitor._accrue_debt_full(reason="test")
  assert not any(
      d.tar_path == "/tmp/2026-03-01.tar" and d.kind == DebtKind.DAY_CLOSE
      for d in janitor._debt_heap
  )


def test_janitor_pop_eligible_debt_requeues_disqualified(monkeypatch):
  janitor = _make_janitor(get_disqualified_daily_tars=lambda: {"/tmp/2026-01-01.tar"})
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: pytest.fail("should not run"))
  janitor._run_tick_body()
  assert janitor.debt_depth() == 1


def test_janitor_partial_accrual_during_ingest_backlog_enqueues_prior_day_raw_debt(monkeypatch):
  janitor = _make_janitor()
  janitor._last_accrual_at = 0.0
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *_a, **_k: {"/tmp/2026-01-01.tar.gz": ["/tmp/raw"]},
  )
  monkeypatch.setattr(janitor, "_calendar_today_local", lambda: date(2026, 2, 1))
  assert janitor.maybe_accrue_partial_debt_if_due(1.0)
  kinds = {d.kind for d in janitor._debt_heap}
  assert DebtKind.DAY_CLOSE in kinds


def test_janitor_raw_remove_15k_files_spans_multiple_ticks_without_debt_loss(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  zst_path = _zst_path_for_tar(tar_path)
  calls = {"n": 0}

  def remaining(*_a, **_k):
    calls["n"] += 1
    if calls["n"] < 6:
      return {zst_path: [f"/tmp/raw-{calls['n']}"]}
    return {}

  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", remaining)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_raw_paths_per_tick", lambda: 1)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._run_tick_body()
  assert janitor.debt_depth() >= 1
  assert calls["n"] >= 1
  ticks = 0
  while janitor.debt_depth() > 0 and ticks < 8:
    janitor._run_tick_body()
    ticks += 1
  assert calls["n"] >= 2
  assert _day_phase_value(janitor._day_phases, tar_path) in ("raw_removed", "tar_dropped")


def test_janitor_debt_depth_decreases_under_burst_with_many_prior_days(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_high_watermark", lambda: 2)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_burst_factor", lambda: 2.0)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  for i in range(6):
    janitor._enqueue_debt(DebtKind.TAR_DROP, f"/tmp/2026-01-{i+1:02d}.tar", persist=False)
  before = janitor.debt_depth()
  janitor._run_tick_body()
  assert janitor.debt_depth() < before


def test_debt_cap_200_does_not_silently_drop_oldest_prior_day_seal_debt(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 2)
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-03.tar", persist=False)
  assert janitor.debt_depth() == 2
  assert janitor.log_fn.called


def test_enqueue_completed_prior_days_reclaim_orders_oldest_first(monkeypatch, tmp_path):
  for day in ("2026-01-01", "2026-01-02"):
    open(tmp_path / f"{day}.tar", "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda d: sorted(
      [str(tmp_path / "2026-01-02.tar"), str(tmp_path / "2026-01-01.tar")],
  ))
  monkeypatch.setattr(janitor, "_calendar_today_local", lambda: date(2026, 1, 3))
  janitor.enqueue_completed_prior_days_reclaim()
  ordered = sorted(janitor._debt_heap, key=lambda d: d.sort_index)
  tar_order = [d.tar_path for d in ordered if d.kind == DebtKind.DAY_CLOSE]
  assert tar_order[0].endswith("2026-01-01.tar")
  assert tar_order[1].endswith("2026-01-02.tar")


def test_janitor_tick_corrupt_tar_recovery_before_raw_remove(monkeypatch, tmp_path):
  """RAW_REMOVE tick wires archive_stats recovery; corrupt tar restore runs without debt loss."""
  import tarfile

  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  tgz = tmp_path / "daily"
  tgz.mkdir()
  tar_path = str(tgz / "2026-01-01.tar")
  zst_path = str(tgz / "2026-01-01.tar.zst")
  open(tar_path, "wb").write(b"corrupt")
  open(zst_path, "wb").write(b"sealed-placeholder")

  restore_calls = []

  def spy_restore(tp, zp, gp, threads):
    del zp, gp, threads
    restore_calls.append(tp)
    member = tmp_path / "member.txt"
    member.write_text("payload")
    with tarfile.open(tp, "w") as tf:
      tf.add(str(member), arcname="stats1")
    return True

  monkeypatch.setattr(helpers, "replace_corrupt_tar_from_compressed_backup", spy_restore)

  def archive_stats_fn(_item):
    if os.path.isfile(tar_path) and not helpers.verify_tar_archive_readable(tar_path):
      return spy_restore(tar_path, zst_path, str(tgz / "2026-01-01.tar.gz"), 1)
    return True

  janitor = _make_janitor(
      tgz_archive_dir=str(tgz),
      archive_stats_files_fn=archive_stats_fn,
      ingest_ready_fn=lambda _path: True,
  )
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})

  def remove_invokes_recovery(*_a, **kwargs):
    fn = kwargs.get("archive_stats_files_fn")
    if fn:
      fn((zst_path, []))

  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", remove_invokes_recovery)
  depth_before = janitor.debt_depth()
  janitor._run_tick_body()
  assert restore_calls
  assert janitor.debt_depth() <= depth_before


def test_oldest_completed_day_reclaimed_before_newer_days(monkeypatch, tmp_path):
  """Janitor tick processes older calendar day seal/raw/tar debt before newer days."""
  events = []
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  for tar in (tar1, tar2):
    open(tar, "wb").close()
    open(tar.replace(".tar", ".tar.zst"), "wb").close()

  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  for tar in (tar2, tar1):
    for kind in (DebtKind.TAR_DROP, DebtKind.RAW_REMOVE, DebtKind.SEAL_PRIOR_DAY):
      janitor._enqueue_debt(kind, tar, persist=False)

  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 6)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  monkeypatch.setattr(
      janitor_mod,
      "atomic_seal_tar_to_zst",
      lambda tar_path, *a, **k: events.append(("seal", tar_path)),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_archived_raw_files",
      lambda *a, **k: events.append(
          ("raw", next(iter(k.get("only_daily_tar_paths") or []), None)),
      ),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_uncompressed_daily_tars",
      lambda *a, **k: events.append(
          ("tar_drop", next(iter(k.get("only_daily_tar_paths") or []), None)),
      ),
  )

  janitor._run_tick_body()
  seal_order = [tar for kind, tar in events if kind == "seal"]
  raw_order = [tar for kind, tar in events if kind == "raw"]
  tar_drop_order = [tar for kind, tar in events if kind == "tar_drop"]
  assert tar1 in seal_order and tar2 in seal_order
  assert seal_order.index(tar1) < seal_order.index(tar2)
  assert raw_order.index(tar1) < raw_order.index(tar2)
  assert tar_drop_order.index(tar1) < tar_drop_order.index(tar2)


def test_janitor_raw_remove_skips_not_head_ingested_raw(monkeypatch, tmp_path):
  """RAW_REMOVE must not delete raw when ingest_ready_fn returns false."""
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  day_epoch = 1700000000
  raw_path = host_dir / str(day_epoch)
  raw_path.write_text("%d host evt 1\n" % day_epoch)

  tgz = tmp_path / "daily"
  tgz.mkdir()
  tar_path = str(tgz / "2026-01-01.tar")
  open(tar_path, "wb").close()
  zst_path = _zst_path_for_tar(tar_path)
  open(zst_path, "wb").close()

  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[str(raw_path)],
      remaining_raw_by_gz={zst_path: [str(raw_path)]},
      mapping={zst_path: [str(raw_path)]},
      ready_paths=set(),
  )

  janitor = _make_janitor(
      archive_data_dir=str(archive_dir),
      tgz_archive_dir=str(tgz),
      ingest_ready_fn=lambda _path: False,
  )
  janitor._accrual_snapshot = snapshot
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "scan_and_quarantine_unparsable_closed_raw", lambda *a, **k: 0)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *a, **k: {zst_path: [str(raw_path)]},
  )
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._run_tick_body()
  assert raw_path.is_file()
  assert _day_phase_value(janitor._day_phases, tar_path) != "raw_removed"


def test_janitor_persist_hints_once_per_tick_with_multiple_debt_items(monkeypatch):
  janitor = ArchiveJanitor(
      archive_data_dir="/tmp/archive",
      host_name_ext=".hpc",
      tgz_archive_dir="/tmp/daily",
      local_tz=timezone.utc,
      log_fn=MagicMock(),
      get_disqualified_daily_tars=lambda: set(),
      get_ingest_backlog_high=lambda: False,
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
      ingest_ready_fn=None,
      archive_stats_files_fn=None,
  )
  persist_mock = MagicMock(wraps=janitor._persist_hints)
  janitor._persist_hints = persist_mock
  janitor._enqueue_debt(DebtKind.VALIDATE, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.VALIDATE, "/tmp/2026-01-02.tar", persist=False)
  monkeypatch.setattr(janitor, "_process_debt_item", lambda *a, **k: True)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 9999)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 10)
  janitor._run_tick_body()
  assert persist_mock.call_count == 1


def test_janitor_tar_drop_blocked_when_parsable_unmapped_closed_raw(
    monkeypatch, tmp_path,
):
  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(str(daily_dir / "2026-01-01.tar.zst"), "wb").close()
  day_epoch = int(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  raw_path = host_dir / str(day_epoch)
  raw_path.write_text("%d job1 cn001\n" % day_epoch)

  def disqualify():
    from hpcperfstats.dbload.sync_timedb_archive_helpers import (
        collect_days_with_unmapped_closed_raw,
        collect_stats_files_in_range,
    )
    closed_paths = collect_stats_files_in_range(
        str(archive_dir), "all", None, ".hpc",
    )
    # Parsable closed raw absent from a stale/partial mapping still blocks the day.
    return collect_days_with_unmapped_closed_raw(
        closed_paths, {}, str(daily_dir),
    )

  janitor = _make_janitor(
      archive_data_dir=str(archive_dir),
      tgz_archive_dir=str(daily_dir),
      host_name_ext=".hpc",
      get_disqualified_daily_tars=disqualify,
  )
  janitor._accrual_snapshot = None
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *_a, **_k: {})
  called = {"drop": False}

  tar_norm = os.path.normpath(tar_path)
  unmapped = disqualify()
  assert tar_norm in unmapped
  from hpcperfstats.dbload.sync_timedb_archive_helpers import (
      is_unparsable_closed_stats_path,
  )
  assert not is_unparsable_closed_stats_path(str(raw_path))

  def fake_drop(*_a, **kwargs):
    skip = {
        os.path.normpath(p)
        for p in (kwargs.get("skip_daily_tar_paths") or ())
    }
    if tar_norm not in skip:
      called["drop"] = True

  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", fake_drop)
  janitor._run_tick_body()
  assert os.path.isfile(tar_path)
  assert called["drop"] is False


def test_janitor_tar_drop_proceeds_after_unparsable_quarantine(
    monkeypatch, tmp_path,
):
  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(str(daily_dir / "2026-01-01.tar.zst"), "wb").close()
  day_epoch = int(datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
  raw_path = host_dir / str(day_epoch)
  raw_path.write_text("not-a-stats-line\n")

  def disqualify():
    from hpcperfstats.dbload.sync_timedb_archive_helpers import (
        collect_unmapped_closed_raw_daily_tars,
    )
    return collect_unmapped_closed_raw_daily_tars(
        str(archive_dir), ".hpc", str(daily_dir),
    )

  janitor = _make_janitor(
      archive_data_dir=str(archive_dir),
      tgz_archive_dir=str(daily_dir),
      host_name_ext=".hpc",
      get_disqualified_daily_tars=disqualify,
  )
  janitor._accrual_snapshot = None
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *_a, **_k: {})
  called = {"drop": False}
  tar_norm = os.path.normpath(tar_path)

  def fake_drop(*_a, **kwargs):
    skip = {
        os.path.normpath(p)
        for p in (kwargs.get("skip_daily_tar_paths") or ())
    }
    if tar_norm not in skip:
      called["drop"] = True

  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", fake_drop)
  janitor._run_tick_body()
  assert not raw_path.exists()
  assert called["drop"] is True


def test_janitor_skips_quarantine_for_pending_inflight_path(tmp_path):
  import hpcperfstats.dbload.sync_timedb_archive_helpers as helpers

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "bad_raw"
  raw_path.write_text("no-timestamp-here\n")
  janitor = _make_janitor(
      archive_data_dir=str(archive_dir),
      host_name_ext=".hpc",
      get_quarantine_skip_paths=lambda: {str(raw_path)},
  )
  janitor._run_unparsable_quarantine_scan()
  assert raw_path.is_file()
  quarantine_root = archive_dir / helpers.SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME
  assert not (quarantine_root / "host.hpc" / "bad_raw").exists()


def test_janitor_raw_remove_deletes_new_closed_raw_after_accrual_snapshot_stale(
    monkeypatch, tmp_path,
):
  from hpcperfstats.dbload.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  archive_dir = tmp_path / "archive"
  host_dir = archive_dir / "host.hpc"
  host_dir.mkdir(parents=True)
  raw_path = host_dir / "stats_new"
  raw_path.write_text("1704067200 host evt 1\n")
  tgz = tmp_path / "daily"
  tgz.mkdir()
  tar_path = str(tgz / "2026-01-01.tar")
  zst_path = str(tgz / "2026-01-01.tar.zst")
  open(tar_path, "wb").close()
  open(zst_path, "wb").close()

  stale_snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
  )
  janitor = _make_janitor(
      archive_data_dir=str(archive_dir),
      tgz_archive_dir=str(tgz),
      host_name_ext=".hpc",
      ingest_ready_fn=lambda _path: True,
  )
  janitor._accrual_snapshot = stale_snapshot
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)

  captured = {}

  def fake_remove(*_a, **kwargs):
    captured.update(kwargs)
    return None

  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", fake_remove)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._run_tick_body()
  assert captured.get("maintenance_snapshot") is None
  assert captured.get("only_daily_tar_paths") == {tar_path}


def test_janitor_skips_debt_item_when_day_becomes_disqualified_mid_tick(monkeypatch):
  tar1 = "/tmp/2026-01-01.tar"
  tar2 = "/tmp/2026-01-02.tar"
  janitor = _make_janitor()
  _mark_day_sealed(janitor, tar1)
  _mark_day_sealed(janitor, tar2)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar1, persist=False)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar2, persist=False)
  calls = []

  def disqualify():
    if calls:
      return {tar2}
    return set()

  janitor.get_disqualified_daily_tars = disqualify

  def fake_remove(tar_path, *_a, **_k):
    calls.append(tar_path)
    return True

  monkeypatch.setattr(janitor, "_raw_remove_one_day", fake_remove)
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 9999)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 2)
  janitor._run_tick_body()
  assert calls == [tar1]


def test_janitor_defer_reenqueue_persists_debt_before_tick_end(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = ArchiveJanitor(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path),
      local_tz=timezone.utc,
      log_fn=MagicMock(),
      get_disqualified_daily_tars=lambda: set(),
      get_ingest_backlog_high=lambda: False,
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
  )
  persist_calls = []
  original_persist = janitor._persist_hints

  def tracking_persist(*args, **kwargs):
    persist_calls.append(1)
    return original_persist(*args, **kwargs)

  janitor._persist_hints = tracking_persist
  zst_path = _zst_path_for_tar(tar_path)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {zst_path: ["/tmp/raw"]},
  )
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  assert janitor._seal_one_day(tar_path) is False
  assert persist_calls
  assert janitor.debt_depth() >= 1


def test_janitor_mtime_accrual_does_not_call_disqualify_under_debt_lock(monkeypatch):
  janitor = _make_janitor(tgz_archive_dir="/tmp/daily")
  calls = {"under_lock": False}
  real_lock = janitor._debt_lock

  class LockProbe:
    def __enter__(self):
      calls["under_lock"] = True
      return real_lock.__enter__()

    def __exit__(self, *args):
      calls["under_lock"] = False
      return real_lock.__exit__(*args)

  janitor._debt_lock = LockProbe()
  disqualify_calls = {"under_lock": 0}

  def disqualify():
    if calls["under_lock"]:
      disqualify_calls["under_lock"] += 1
    return set()

  janitor.get_disqualified_daily_tars = disqualify
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda *_a: [])
  janitor._accrue_debt_mtime_scan(reason="test")
  assert disqualify_calls["under_lock"] == 0


def test_day_complete_reclaim_enqueues_only_newly_completed_days(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  tar_path = str(daily_dir / "2020-01-05.tar")
  janitor.enqueue_completed_prior_days_reclaim(chunk_daily_tars={tar_path})
  kinds = {debt.kind for debt in janitor._debt_heap}
  assert kinds == {DebtKind.DAY_CLOSE}
  assert janitor.debt_depth() == 1


def test_close_one_day_runs_seal_raw_tar_in_single_debt_item(monkeypatch, tmp_path):
  events = []
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(tar_path.replace(".tar", ".tar.zst"), "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(
      janitor_mod,
      "atomic_seal_tar_to_zst",
      lambda tp, *a, **k: events.append(("seal", tp)),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_archived_raw_files",
      lambda *a, **k: events.append(
          ("raw", next(iter(k.get("only_daily_tar_paths") or []), None)),
      ),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_uncompressed_daily_tars",
      lambda *a, **k: events.append(
          ("tar", next(iter(k.get("only_daily_tar_paths") or []), None)),
      ),
  )
  janitor._run_tick_body()
  kinds = [e[0] for e in events]
  assert kinds == ["seal", "raw", "tar"]


def test_janitor_days_per_tick_limits_distinct_calendar_days(monkeypatch, tmp_path):
  events = []
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  for tar in (tar1, tar2):
    open(tar, "wb").close()
    open(tar.replace(".tar", ".tar.zst"), "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  for tar in (tar1, tar2):
    for kind in (DebtKind.TAR_DROP, DebtKind.RAW_REMOVE, DebtKind.SEAL_PRIOR_DAY):
      janitor._enqueue_debt(kind, tar, persist=False)
  assert janitor.debt_depth() == 2
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick", lambda: 2)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  monkeypatch.setattr(
      janitor_mod,
      "atomic_seal_tar_to_zst",
      lambda tp, *a, **k: events.append(("seal", tp)),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_archived_raw_files",
      lambda *a, **k: events.append(
          ("raw", next(iter(k.get("only_daily_tar_paths") or []), None)),
      ),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_uncompressed_daily_tars",
      lambda *a, **k: events.append(
          ("tar", next(iter(k.get("only_daily_tar_paths") or []), None)),
      ),
  )
  janitor._run_tick_body()
  sealed = [e[1] for e in events if e[0] == "seal"]
  assert tar1 in sealed
  assert tar2 in sealed


def test_enqueue_day_close_for_drained_days(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_old = str(daily_dir / "2026-01-01.tar")
  tar_new = str(daily_dir / "2026-01-02.tar")
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  janitor.enqueue_day_close_for_drained_days(
      {tar_old, tar_new},
      {tar_new},
  )
  assert janitor.debt_depth() == 1
  debt = janitor._debt_heap[0]
  assert debt.kind == DebtKind.DAY_CLOSE
  assert debt.tar_path == os.path.normpath(tar_old)


def test_janitor_persist_hints_snapshots_day_phases_under_lock(monkeypatch, tmp_path):
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb_archive_maint.cfg.get_sync_archive_maint_hints",
      lambda: True,
  )
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir, exist_ok=True)
  janitor = ArchiveJanitor(
      archive_data_dir=archive_dir,
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=timezone.utc,
      log_fn=MagicMock(),
      get_disqualified_daily_tars=lambda: set(),
      get_ingest_backlog_high=lambda: False,
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
  )
  tar_path = str(tmp_path / "daily" / "2026-01-01.tar")
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  open(tar_path, "wb").close()
  with janitor._hints_state_lock:
    janitor._day_phases[tar_path] = "sealed"
  janitor._persist_hints()
  loaded = janitor_mod.load_archive_maint_hints(archive_dir)
  assert loaded is not None
  assert tar_path in loaded.get("day_phases", {})
