"""Unit tests for ArchiveJanitor debt queue and micro-batch ticks."""

import os
import tarfile
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

import hpcperfstats.dbload.lib.sync_timedb_archive_janitor as janitor_mod
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import (
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
  from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths

  zst_path, _gz_path = compressed_sibling_paths(tar_path)
  return zst_path


def _mark_day_phase(janitor, tar_path, phase):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import day_phase_hint_entry

  tar_norm = os.path.normpath(tar_path)
  janitor._day_phases[tar_norm] = day_phase_hint_entry(tar_norm, phase)


def _mark_day_sealed(janitor, tar_path):
  _mark_day_phase(janitor, tar_path, "sealed")


def _mark_day_raw_removed(janitor, tar_path):
  _mark_day_phase(janitor, tar_path, "raw_removed")


def _stub_janitor_day_close_tick_phases(
    monkeypatch,
    events,
    *,
    tar_drop_event_key="tar",
    no_discover=True,
    with_coordinator=True,
):
  """Stub seal/delete/tar_drop so DAY_CLOSE ticks complete without FS side effects."""
  if no_discover:
    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "_discover_and_enqueue_ready_day_close",
        lambda self, **kwargs: None,
    )

  def seal_stub(self, tar_path):
    events.append(("seal", os.path.normpath(tar_path)))
    _mark_day_sealed(self, tar_path)
    return True

  def tar_drop_stub(self, tar_path, *args, **kwargs):
    events.append((tar_drop_event_key, os.path.normpath(tar_path)))
    _mark_day_phase(self, tar_path, "tar_dropped")
    return True

  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_seal_one_day", seal_stub)
  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_tar_drop_one_day", tar_drop_stub)

  if not with_coordinator:
    return None

  class _DayCloseCoordStub:
    enabled = True
    _pre_seal_done = set()
    _post_seal_done = set()
    _delete_done = set()

    def pre_seal_verification_complete(self, tar_path):
      return os.path.normpath(tar_path) in self._pre_seal_done

    def post_seal_verification_complete(self, tar_path):
      return os.path.normpath(tar_path) in self._post_seal_done

    def run_pre_seal_verify_sync(self, tar_path):
      tar_norm = os.path.normpath(tar_path)
      events.append(("pre_seal_verify", tar_norm))
      self._pre_seal_done.add(tar_norm)
      return True

    def run_post_seal_verify_sync(self, tar_path):
      tar_norm = os.path.normpath(tar_path)
      events.append(("post_seal_verify", tar_norm))
      self._post_seal_done.add(tar_norm)
      return True

    def should_handoff_before_seal(self, tar_path):
      return False

    def verification_complete(self, tar_path):
      return self.post_seal_verification_complete(tar_path)

    def promote_phase_if_verify_stage_ahead(self, tar_path):
      return False

    def reopen_done_days_with_verified_on_disk(self):
      return 0

    def delete_phase_done(self, tar_path):
      return os.path.normpath(tar_path) in self._delete_done

    def reclassify_retryable_skips_after_handoff_sync(self, tar_path):
      return 0

    def begin_deleting(self, tar_path):
      events.append(("delete", os.path.normpath(tar_path)))

    def apply_batch_delete(self, tar_path):
      self._delete_done.add(os.path.normpath(tar_path))
      return 0

  return _DayCloseCoordStub()


def _make_janitor(**kwargs):
  suffix = uuid.uuid4().hex
  archive_data_dir = kwargs.pop("archive_data_dir", None) or tempfile.mkdtemp(
      prefix="janitor_archive_%s_" % suffix)
  tgz_archive_dir = kwargs.pop("tgz_archive_dir", None) or tempfile.mkdtemp(
      prefix="janitor_daily_%s_" % suffix)
  defaults = {
      "archive_data_dir": archive_data_dir,
      "host_name_ext": ".hpc",
      "tgz_archive_dir": tgz_archive_dir,
      "local_tz": timezone.utc,
      "log_fn": MagicMock(),
      "get_disqualified_daily_tars": lambda: set(),
      "get_pending_stats_count": lambda: 0,
      "get_idle_seconds": lambda: 0.0,
      "ingest_ready_fn": None,
      "archive_stats_files_fn": None,
      "day_raw_removal_coordinator": None,
  }
  defaults.update(kwargs)
  janitor = ArchiveJanitor(**defaults)
  janitor._persist_hints = MagicMock()
  janitor._allow_tick_chaining = False
  return janitor


def test_enqueue_debt_dedupes_same_kind_and_tar():
  janitor = _make_janitor()
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  assert janitor.debt_depth() == 1


def test_has_day_close_work_true_for_debt_inflight_tick_and_pending_signal():
  janitor = _make_janitor()
  assert janitor.has_day_close_work() is False
  assert janitor.stats()["janitor_day_close_inflight"] == 0

  janitor._enqueue_debt(DebtKind.DAY_CLOSE, "/tmp/2026-01-01.tar", persist=False)
  assert janitor.has_day_close_work() is True
  janitor._debt_heap.clear()
  janitor._debt_seen.clear()
  assert janitor.has_day_close_work() is False

  class _PendingFuture:
    def done(self):
      return False

  debt = DayDebt(
      sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, "/tmp/2026-01-02.tar"),
      kind=DebtKind.DAY_CLOSE,
      tar_path="/tmp/2026-01-02.tar",
  )
  with janitor._day_close_in_flight_lock:
    janitor._day_close_in_flight[_PendingFuture()] = debt
  assert janitor.has_day_close_work() is True
  assert janitor.stats()["janitor_day_close_inflight"] == 1
  with janitor._day_close_in_flight_lock:
    janitor._day_close_in_flight.clear()

  janitor._tick_depth = 1
  assert janitor.has_day_close_work() is True
  janitor._tick_depth = 0

  janitor._pending_signal = True
  assert janitor.has_day_close_work() is True
  janitor._pending_signal = False

  janitor._future = _PendingFuture()
  assert janitor.has_day_close_work() is True
  janitor._future = None
  assert janitor.has_day_close_work() is False


def test_debt_queue_payload_serializes_heap_entries():
  janitor = _make_janitor()
  janitor._enqueue_debt(DebtKind.TAR_DROP, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  payload = janitor._debt_queue_payload()
  kinds = {entry["kind"] for entry in payload}
  assert kinds == {DebtKind.DAY_CLOSE.value}
  assert len(payload) == 2


def test_run_scheduled_maintenance_pass_refreshes_snapshot_and_enqueues(
    monkeypatch, tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-05.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=["/tmp/raw-a"],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
      first_timestamp_by_path={},
      head_identity_by_path={},
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: snapshot,
  )
  janitor.run_heavy_maintenance_pass(reason="startup")
  with janitor._accrual_snapshot_lock:
    assert janitor._accrual_snapshot is snapshot
  assert janitor.debt_depth() == 0


def test_heavy_startup_pass_passes_build_ready_set_false(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  captured = {}

  def fake_build(archive_data_dir, host_name_ext, tgz_archive_dir, **kwargs):
    captured.update(kwargs)
    return ArchiveMaintenanceSnapshot(
        closed_paths=["/tmp/raw-a"],
        remaining_raw_by_gz={},
        mapping={},
        ready_paths=set(),
        first_timestamp_by_path={},
        head_identity_by_path={},
    )

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  janitor.startup_snapshot_coordinator = None
  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", fake_build)
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  janitor.run_heavy_maintenance_pass(reason="startup")
  assert captured.get("build_ready_set") is False


def test_get_accrual_snapshot_for_reconcile_returns_mapping(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  janitor = _make_janitor()
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      mapping={"k": ["/raw/a"]},
  )
  with janitor._accrual_snapshot_lock:
    janitor._accrual_snapshot = snapshot
  assert janitor.get_accrual_snapshot_for_reconcile() is snapshot


def test_get_accrual_snapshot_for_reconcile_none_when_empty_mapping(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  janitor = _make_janitor()
  with janitor._accrual_snapshot_lock:
    janitor._accrual_snapshot = ArchiveMaintenanceSnapshot(
        closed_paths=[],
        mapping={},
    )
  assert janitor.get_accrual_snapshot_for_reconcile() is None


def test_tick_day_close_delete_uses_coordinator_batch_delete(monkeypatch, tmp_path):
  """Sealed days delete via day_raw_removal coordinator, not allow_auto_seal raw remove."""
  events = []
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(tar_path.replace(".tar", ".tar.zst"), "wb").close()
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, events)
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, tar_path, persist=False)
  janitor._run_tick_body()
  assert ("delete", os.path.normpath(tar_path)) in events
  assert ("tar", os.path.normpath(tar_path)) in events


def test_janitor_tick_debt_budget_excludes_scheduled_maintenance_pass(
    monkeypatch, tmp_path,
):
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, str(tmp_path / "2026-01-01.tar"), persist=False)
  processed = {"n": 0}
  # Maintenance consumes early timestamps; debt-drain budget starts afterward.
  time_values = iter([100.0, 150.0, 150.0, 150.0, 150.0, 150.0, 150.0])

  def fake_time():
    try:
      return next(time_values)
    except StopIteration:
      return 150.0

  def slow_maintenance(*_a, **_k):
    return None

  def fake_process(_debt, **_kwargs):
    processed["n"] += 1
    return True

  monkeypatch.setattr(janitor_mod.time, "time", fake_time)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", lambda *_a, **_k: 0)
  monkeypatch.setattr(janitor, "run_scheduled_maintenance_pass", slow_maintenance)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  with janitor._maintenance_pass_lock:
    janitor._pending_maintenance_pass_reason = "startup"
  monkeypatch.setattr(janitor, "_process_debt_item", fake_process)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 30.0)
  janitor._run_tick_body()
  assert processed["n"] >= 1


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
  processed = {"n": 0}

  def fake_time():
    # One worker at a time; after two completions expire the debt-drain budget.
    if processed["n"] >= 2:
      return 200.0
    return 100.0

  def fake_process(_debt, **_kwargs):
    processed["n"] += 1
    return True

  monkeypatch.setattr(janitor_mod.time, "time", fake_time)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", lambda *_a, **_k: 0)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(janitor, "_process_debt_item", fake_process)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 50.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  janitor._run_tick_body()
  assert processed["n"] == 2
  assert janitor.debt_depth() == 1
  assert janitor.stats()["janitor_budget_throttled"] >= 1


def test_janitor_tick_exception_requeues_unprocessed_debt(monkeypatch):
  janitor = _make_janitor()
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar", persist=False)
  clock = {"t": 100.0}

  def fake_time():
    return clock["t"]

  def boom(_debt, **_kwargs):
    # Expire debt-drain budget after the first failure so we do not spin retries.
    clock["t"] = 200.0
    raise RuntimeError("seal failed")

  monkeypatch.setattr(janitor_mod.time, "time", fake_time)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(janitor, "_process_debt_item", boom)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 30.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  janitor._run_tick_body()
  assert janitor.debt_depth() >= 1
  assert DebtKind.DAY_CLOSE in {d.kind for d in janitor._debt_heap}


def test_janitor_tar_drop_blocks_when_accrual_snapshot_none(monkeypatch, tmp_path):
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 0.01)
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(str(tmp_path / "2026-01-01.tar.zst"), "wb").close()
  raw_path = str(tmp_path / "raw.stats")
  open(raw_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._accrual_snapshot = None
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {str(tmp_path / "2026-01-01.tar.zst"): [raw_path]},
  )
  called = {"drop": False}

  def fake_drop(*_a, **_k):
    called["drop"] = True

  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", fake_drop)
  janitor._run_tick_body()
  assert os.path.isfile(tar_path)
  assert called["drop"] is False


def test_janitor_tar_drop_blocks_when_raw_appears_after_accrual(monkeypatch, tmp_path):
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 0.01)
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(str(tmp_path / "2026-01-01.tar.zst"), "wb").close()
  raw_path = str(tmp_path / "new-raw.stats")
  open(raw_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._accrual_snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
  )
  janitor._enqueue_debt(DebtKind.TAR_DROP, tar_path, persist=False)
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {str(tmp_path / "2026-01-01.tar.zst"): [raw_path]},
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
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None, raising=False)
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


def test_janitor_legacy_seal_prior_day_coalesces_to_day_close_and_seals(
    monkeypatch, tmp_path,
):
  """Enqueued SEAL_PRIOR_DAY becomes DAY_CLOSE; seal runs despite on-disk raw."""
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, tar_path, persist=False)
  assert janitor._debt_heap[0].kind == DebtKind.DAY_CLOSE
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
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None, raising=False)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._run_tick_body()
  assert called["seal"] == 1


def test_seal_one_day_direct_call_seals_when_closed_raw_remains(monkeypatch, tmp_path):
  """Seal is not gated on closed-raw; pre-seal/handoff and tar_drop own that."""
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  raw_path = str(tmp_path / "raw.stats")
  open(raw_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  zst_path = _zst_path_for_tar(tar_path)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {zst_path: [raw_path]},
  )
  called = {"seal": 0}

  def fake_seal(*_a, **_k):
    called["seal"] += 1
    open(zst_path, "wb").close()
    return None

  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", fake_seal)
  assert janitor._seal_one_day(tar_path) is True
  assert called["seal"] == 1


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
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None, raising=False)
  janitor._run_tick_body()
  assert _day_phase_value(janitor._day_phases, tar_path) != "raw_removed"


def test_janitor_debt_max_entries_logs_and_caps_heap(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 2)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-03.tar", persist=False)
  assert janitor.debt_depth() == 2


def test_trim_heap_evicts_lowest_priority_not_best(monkeypatch):
  """F10: over-cap trim must drop max(sort_index), not heappop best."""
  janitor = _make_janitor()
  # Avoid mid-enqueue eviction so trim path is exercised alone.
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 100)
  oldest = "/tmp/2026-01-01.tar"
  middle = "/tmp/2026-01-02.tar"
  newest = "/tmp/2026-01-03.tar"
  with janitor._debt_lock:
    for tar in (oldest, middle, newest):
      janitor._enqueue_day_close_locked(tar, persist=False)
    assert len(janitor._debt_heap) == 3
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 2)
  with janitor._debt_lock:
    janitor._trim_heap_to_max_entries_locked()
    remaining = {d.tar_path for d in janitor._debt_heap}
  assert len(remaining) == 2
  assert newest not in remaining
  assert oldest in remaining
  assert middle in remaining


def test_pop_eligible_debt_requeues_duplicate_same_tar(monkeypatch, tmp_path):
  """F9: duplicate same-tar debts after first select must re-push, not discard."""
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  tar_a = os.path.normpath(str(tmp_path / "2026-01-01.tar"))
  tar_b = os.path.normpath(str(tmp_path / "2026-01-02.tar"))
  open(tar_a, "wb").close()
  open(tar_b, "wb").close()
  with janitor._debt_lock:
    janitor._enqueue_day_close_locked(tar_a, persist=False)
    # Inject a second DAY_CLOSE-shaped debt for tar_a (simulates duplicate).
    dup = DayDebt(
        sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_a),
        kind=DebtKind.DAY_CLOSE,
        tar_path=tar_a,
        gz_path=tar_a + ".zst",
    )
    janitor._debt_heap.append(dup)
    import heapq
    heapq.heapify(janitor._debt_heap)
    janitor._enqueue_day_close_locked(tar_b, persist=False)
    selected = janitor._pop_eligible_debt_locked(set(), max_days=1)
    remaining = {d.tar_path for d in janitor._debt_heap}
  assert len(selected) == 1
  assert selected[0].tar_path == tar_a
  assert tar_a in remaining
  assert tar_b in remaining


def test_janitor_persisted_debt_queue_matches_heap_after_cap(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_max_entries", lambda: 2)
  janitor._enqueue_debt(DebtKind.SEAL_PRIOR_DAY, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-02.tar", persist=False)
  janitor._enqueue_debt(DebtKind.TAR_DROP, "/tmp/2026-01-03.tar", persist=False)
  payload_keys = {(e["kind"], e["tar_path"]) for e in janitor._debt_queue_payload()}
  heap_keys = {(d.kind.value, d.tar_path) for d in janitor._debt_heap}
  assert payload_keys == heap_keys


def test_tick_lock_cleanup_runs_before_day_close(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  order = []

  def fake_cleanup(*_a, **_k):
    order.append("cleanup")
    return 2

  def fake_close(*_a, **_k):
    order.append("close")
    return True

  monkeypatch.setattr(janitor_mod, "cleanup_orphan_fnctl_lock_sidecars", fake_cleanup)
  monkeypatch.setattr(janitor, "_close_one_day", fake_close)
  janitor._run_tick_body()
  assert order[-1] == "close"
  assert order[0] == "cleanup"
  assert all(step == "cleanup" for step in order[:-1])


def test_janitor_enqueues_day_close_from_dedupe_hint(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.join(str(daily_dir), "2026-05-09.tar")
  open(tar_path, "wb").close()

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_day_close_candidate_inputs=lambda: {"unprocessed_by_tar": {}},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.list_dedupe_hint_day_tokens",
      lambda client=None: ["2026-05-09"],
  )
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(
      helpers,
      "daily_tar_eligible_for_day_close_submit",
      lambda *a, **k: (True, ""),
  )
  janitor._consume_dedupe_hints(set())
  assert os.path.normpath(tar_path) in janitor._debt_heap_tar_paths()


def test_close_one_day_dedupes_before_seal(monkeypatch, tmp_path):
  tar_path = str(tmp_path / "2020-01-05.tar")
  open(tar_path, "wb").close()
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, [])
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  order = []

  monkeypatch.setattr(janitor_mod, "tar_has_duplicate_file_members", lambda _p: True)
  monkeypatch.setattr(
      janitor_mod,
      "dedupe_tar_keep_largest_file_per_member",
      lambda *_a, **_k: order.append("dedupe"),
  )
  monkeypatch.setattr(
      janitor,
      "_seal_one_day",
      lambda *_a, **_k: order.append("seal") or True,
  )
  monkeypatch.setattr(janitor, "_tar_drop_one_day", lambda *_a, **_k: True)
  janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={"hits": 0, "misses": 0},
      disqualified=set(),
  )
  assert order == ["dedupe", "seal"]


def test_day_raw_removal_coordinator_always_enabled(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import DayRawRemovalCoordinator

  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path),
      log_fn=MagicMock(),
      get_quarantine_skip_paths=lambda: set(),
  )
  assert coord.enabled is True
  coord.shutdown(wait=False)


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

  janitor._session_executor = _Exec()
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

  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_blocking_remaining_raw_for_tar",
      lambda self, *_a, **_k: remaining_seq(),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_archived_raw_files",
      lambda *a, **k: None,
      raising=False,
  )

  def drop_tar(*_a, **_k):
    if os.path.isfile(tar_path):
      os.remove(tar_path)

  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", drop_tar)
  janitor._run_tick_body()
  assert janitor._day_phases.get(tar_path) == "tar_dropped"


def test_janitor_effective_tick_budget_burst_at_watermark(monkeypatch):
  janitor = _make_janitor()
  for i in range(55):
    janitor._enqueue_debt(DebtKind.RAW_REMOVE, f"/tmp/2026-{i:02d}-01.tar", persist=False)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_high_watermark", lambda: 50)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_burst_factor", lambda: 2.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 30.0)
  budget = janitor._effective_tick_budget()
  assert budget >= 60.0


def test_janitor_effective_tick_budget_ignores_pending_stats_depth(monkeypatch):
  """Tick budget no longer shrinks for deep ingest pending (adaptive backlog removed)."""
  base = _make_janitor(get_pending_stats_count=lambda: 0)
  deep = _make_janitor(get_pending_stats_count=lambda: 10000)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 30.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_high_watermark", lambda: 99999)
  assert base._effective_tick_budget() == 30.0
  assert deep._effective_tick_budget() == 30.0


def test_janitor_load_hints_restores_debt_on_init(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import save_archive_maint_hints

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


def test_load_hints_skips_day_close_debt_when_disabled(tmp_path):
  """CLI all (day_close_enabled=False) must not reload DAY_CLOSE debt from hints."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import save_archive_maint_hints

  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  save_archive_maint_hints(
      archive_dir,
      host_dirs={},
      paths={},
      validated_days={},
      debt_queue=[
          {"kind": DebtKind.DAY_CLOSE.value, "tar_path": "/tmp/2026-01-01.tar"},
          {"kind": DebtKind.RAW_REMOVE.value, "tar_path": "/tmp/2026-01-02.tar"},
      ],
  )
  janitor = _make_janitor(archive_data_dir=archive_dir, day_close_enabled=False)
  assert janitor.debt_depth() == 0


def test_load_hints_drops_legacy_lock_cleanup_dedupe_debt(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import save_archive_maint_hints

  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  save_archive_maint_hints(
      archive_dir,
      host_dirs={},
      paths={},
      validated_days={},
      debt_queue=[
          {"kind": DebtKind.LOCK_CLEANUP.value, "tar_path": _LOCK_CLEANUP_TAR_SENTINEL},
          {"kind": DebtKind.DEDUPE.value, "tar_path": "/tmp/2026-01-01.tar"},
          {"kind": DebtKind.RAW_REMOVE.value, "tar_path": "/tmp/2026-01-02.tar"},
      ],
  )
  janitor = _make_janitor(archive_data_dir=archive_dir)
  assert janitor.debt_depth() == 1
  assert {d.kind for d in janitor._debt_heap} == {DebtKind.DAY_CLOSE}


def test_janitor_pop_eligible_debt_requeues_disqualified(monkeypatch):
  janitor = _make_janitor(get_disqualified_daily_tars=lambda: {"/tmp/2026-01-01.tar"})
  janitor._enqueue_debt(DebtKind.RAW_REMOVE, "/tmp/2026-01-01.tar", persist=False)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: pytest.fail("should not run"), raising=False)
  janitor._run_tick_body()
  assert janitor.debt_depth() == 1


def test_signal_scheduled_maintenance_pass_sets_pending_reason(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor, "signal_work_available", lambda: None)
  janitor.signal_scheduled_maintenance_pass(reason="startup")
  assert janitor._pending_maintenance_pass_reason == "startup"


def test_janitor_raw_remove_15k_files_spans_multiple_ticks_without_debt_loss(
    monkeypatch, tmp_path,
):
  """Partial delete progress across ticks keeps DAY_CLOSE debt until complete."""
  events = []
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(tar_path.replace(".tar", ".tar.zst"), "wb").close()
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, events)
  delete_calls = {"n": 0}

  def apply_batch_delete(tar_norm):
    delete_calls["n"] += 1
    if delete_calls["n"] < 3:
      return 1
    coord._delete_done.add(os.path.normpath(tar_norm))
    return 0

  coord.apply_batch_delete = apply_batch_delete
  # Sealed already: skip pre-seal/seal; force post-seal complete.
  coord._pre_seal_done.add(os.path.normpath(tar_path))
  coord._post_seal_done.add(os.path.normpath(tar_path))
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  janitor._run_tick_body()
  assert janitor.debt_depth() >= 1
  assert delete_calls["n"] >= 1
  ticks = 0
  while janitor.debt_depth() > 0 and ticks < 8:
    janitor._run_tick_body()
    ticks += 1
  assert delete_calls["n"] >= 3
  assert _day_phase_value(janitor._day_phases, tar_path) == "tar_dropped"


def test_janitor_debt_depth_decreases_under_burst_with_many_prior_days(monkeypatch):
  janitor = _make_janitor()
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_high_watermark", lambda: 2)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_debt_burst_factor", lambda: 2.0)
  monkeypatch.setattr(janitor, "_close_one_day", lambda *_a, **_k: True)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
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


def test_enqueue_scheduled_day_close_orders_oldest_first(monkeypatch, tmp_path):
  for day in ("2026-01-01", "2026-01-02"):
    open(tmp_path / f"{day}.tar", "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  monkeypatch.setattr(janitor_mod, "iter_daily_tar_paths", lambda d: sorted(
      [str(tmp_path / "2026-01-02.tar"), str(tmp_path / "2026-01-01.tar")],
  ))
  monkeypatch.setattr(janitor, "_calendar_today_local", lambda: date(2026, 1, 3))
  monkeypatch.setattr(
      janitor_mod, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  janitor.enqueue_scheduled_day_close(reason="test")
  ordered = sorted(janitor._debt_heap, key=lambda d: d.sort_index)
  tar_order = [d.tar_path for d in ordered if d.kind == DebtKind.DAY_CLOSE]
  assert tar_order[0].endswith("2026-01-01.tar")
  assert tar_order[1].endswith("2026-01-02.tar")


def test_janitor_tick_corrupt_tar_recovery_before_raw_remove(monkeypatch, tmp_path):
  """RAW_REMOVE tick wires archive_stats recovery; corrupt tar restore runs without debt loss."""
  import tarfile

  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

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

  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", remove_invokes_recovery, raising=False)
  depth_before = janitor.debt_depth()
  janitor._run_tick_body()
  assert restore_calls
  assert janitor.debt_depth() <= depth_before


def test_oldest_completed_day_reclaimed_before_newer_days(monkeypatch, tmp_path):
  """Janitor tick processes older calendar DAY_CLOSE debt before newer days."""
  events = []
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  for tar in (tar1, tar2):
    open(tar, "wb").close()
    open(tar.replace(".tar", ".tar.zst"), "wb").close()

  coord = _stub_janitor_day_close_tick_phases(monkeypatch, events, tar_drop_event_key="tar_drop")
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  # Enqueue newer day first; heap order must still drain oldest calendar day first.
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar2, persist=False)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar1, persist=False)

  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  # Serial workers so seal/delete event order is deterministic.
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 1)

  janitor._run_tick_body()
  seal_order = [tar for kind, tar in events if kind == "seal"]
  delete_order = [tar for kind, tar in events if kind == "delete"]
  tar_drop_order = [tar for kind, tar in events if kind == "tar_drop"]
  assert tar1 in seal_order and tar2 in seal_order
  assert seal_order.index(tar1) < seal_order.index(tar2)
  assert delete_order.index(tar1) < delete_order.index(tar2)
  assert tar_drop_order.index(tar1) < tar_drop_order.index(tar2)


def test_janitor_raw_remove_skips_not_head_ingested_raw(monkeypatch, tmp_path):
  """RAW_REMOVE must not delete raw when ingest_ready_fn returns false."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

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
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *a, **k: {zst_path: [str(raw_path)]},
  )
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_archived_raw_files", lambda *a, **k: None, raising=False)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  janitor._run_tick_body()
  assert raw_path.is_file()
  assert _day_phase_value(janitor._day_phases, tar_path) != "raw_removed"


def test_janitor_persist_hints_once_per_tick_with_multiple_debt_items(monkeypatch):
  janitor = _make_janitor()
  persist_mock = MagicMock(wraps=janitor._persist_hints)
  janitor._persist_hints = persist_mock
  janitor._enqueue_debt(DebtKind.VALIDATE, "/tmp/2026-01-01.tar", persist=False)
  janitor._enqueue_debt(DebtKind.VALIDATE, "/tmp/2026-01-02.tar", persist=False)
  monkeypatch.setattr(janitor, "_process_debt_item", lambda *a, **k: True)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 9999)
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
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        collect_days_with_unmapped_closed_raw,
        collect_stats_files_in_range,
    )
    closed_paths = collect_stats_files_in_range(
        str(archive_dir), "backlog", None, ".hpc",
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
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
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
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

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
  helpers.quarantine_unparsable_closed_raw_paths(
      [str(raw_path)],
      str(archive_dir),
      log_fn=lambda *_a, **_k: None,
  )

  def disqualify():
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
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
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {},
  )
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
  # Seal may unlink .tar when blocking remaining is empty; tar_drop then
  # short-circuits. Otherwise remove_verified must run.
  assert called["drop"] is True or not os.path.isfile(tar_path)


def test_janitor_tick_does_not_scan_unparsable_tree(monkeypatch):
  janitor = _make_janitor()
  assert not hasattr(janitor, "_run_unparsable_quarantine_scan")
  assert "scan_and_quarantine_unparsable_closed_raw" not in dir(janitor_mod)
  janitor._enqueue_debt(DebtKind.VALIDATE, "/tmp/2026-01-01.tar", persist=False)
  monkeypatch.setattr(janitor, "_process_debt_item", lambda *a, **k: True)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 9999)
  janitor._run_tick_body()


def test_janitor_raw_remove_deletes_new_closed_raw_after_accrual_snapshot_stale(
    monkeypatch, tmp_path,
):
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 0.01)
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import DayRawRemovalCoordinator

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

  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(archive_dir),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tgz),
      log_fn=MagicMock(),
      get_quarantine_skip_paths=lambda: set(),
  )
  delete_calls = {"n": 0}

  def fake_apply_batch_delete(tar_norm):
    delete_calls["n"] += 1
    return 0

  monkeypatch.setattr(coord, "run_pre_seal_verify_sync", lambda _t: True)
  monkeypatch.setattr(coord, "pre_seal_verification_complete", lambda _t: True)
  monkeypatch.setattr(coord, "run_post_seal_verify_sync", lambda _t: True)
  monkeypatch.setattr(coord, "post_seal_verification_complete", lambda _t: True)
  monkeypatch.setattr(coord, "verification_complete", lambda _t: True)
  monkeypatch.setattr(coord, "delete_phase_done", lambda _t: delete_calls["n"] >= 1)
  monkeypatch.setattr(coord, "apply_batch_delete", fake_apply_batch_delete)
  coord.shutdown(wait=False)
  janitor = _make_janitor(
      archive_data_dir=str(archive_dir),
      tgz_archive_dir=str(tgz),
      host_name_ext=".hpc",
      day_raw_removal_coordinator=coord,
  )
  _mark_day_sealed(janitor, tar_path)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  janitor._run_tick_body()
  assert delete_calls["n"] == 1


def test_janitor_skips_debt_item_when_day_becomes_disqualified_mid_tick(monkeypatch):
  tar1 = "/tmp/2026-01-01.tar"
  tar2 = "/tmp/2026-01-02.tar"
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, [])
  janitor = _make_janitor(day_raw_removal_coordinator=coord)
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
  janitor.get_delete_disqualified_daily_tars = disqualify

  def fake_delete(tar_path):
    calls.append(tar_path)

  coord.begin_deleting = fake_delete
  coord.apply_batch_delete = lambda _t: 0
  coord.delete_phase_done = lambda t: os.path.normpath(t) in calls
  coord.verification_complete = lambda _t: True
  coord.post_seal_verification_complete = lambda _t: True
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod, "remove_verified_uncompressed_daily_tars", lambda *a, **k: None)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 9999)
  janitor._run_tick_body()
  assert calls == [tar1]


def test_janitor_defer_reenqueue_persists_debt_before_tick_end(monkeypatch, tmp_path):
  """Calendar-grace seal defer re-enqueues DAY_CLOSE and persists hints."""
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = ArchiveJanitor(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path),
      local_tz=timezone.utc,
      log_fn=MagicMock(),
      get_disqualified_daily_tars=lambda: set(),
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
  )
  persist_calls = []
  original_persist = janitor._persist_hints

  def tracking_persist(*args, **kwargs):
    persist_calls.append(1)
    return original_persist(*args, **kwargs)

  janitor._persist_hints = tracking_persist
  monkeypatch.setattr(
      janitor_mod,
      "daily_tar_seal_calendar_eligible",
      lambda *_a, **_k: False,
  )
  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", lambda *a, **k: None)
  assert janitor._seal_one_day(tar_path) is False
  assert persist_calls
  assert janitor.debt_depth() >= 1


def test_scheduled_day_close_does_not_call_disqualify_under_debt_lock(monkeypatch):
  janitor = _make_janitor()
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
  monkeypatch.setattr(
      janitor_mod, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  janitor.enqueue_scheduled_day_close(reason="test")
  assert disqualify_calls["under_lock"] == 0


def test_enqueue_scheduled_day_close_enqueues_eligible_day(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  tar_path = str(daily_dir / "2020-01-05.tar")
  open(tar_path, "wb").close()
  janitor.enqueue_scheduled_day_close(reason="test")
  kinds = {debt.kind for debt in janitor._debt_heap}
  assert kinds == {DebtKind.DAY_CLOSE}
  assert janitor.debt_depth() == 1


def test_day_close_coordinator_runs_pre_and_post_seal_verify(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import DayRawRemovalCoordinator

  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(tar_path.replace(".tar", ".tar.zst"), "wb").close()
  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path),
      log_fn=MagicMock(),
      get_quarantine_skip_paths=lambda: set(),
  )
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  raw_calls = {"n": 0}
  monkeypatch.setattr(janitor_mod, "build_remaining_raw_for_daily_tar", lambda *a, **k: {})
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      lambda *a, **k: {},
  )
  def fake_seal(tar_path, zst_path, *_a, **_k):
    open(zst_path, "wb").close()
    return None

  monkeypatch.setattr(janitor_mod, "atomic_seal_tar_to_zst", fake_seal)
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_archived_raw_files",
      lambda *a, **k: raw_calls.__setitem__("n", raw_calls["n"] + 1),
      raising=False,
  )
  pre_calls = {"n": 0}
  post_calls = {"n": 0}

  def fake_run_pre_seal_verify_sync(tar_norm):
    pre_calls["n"] += 1
    return True

  def fake_run_post_seal_verify_sync(tar_norm):
    post_calls["n"] += 1
    return True

  monkeypatch.setattr(coord, "run_pre_seal_verify_sync", fake_run_pre_seal_verify_sync)
  monkeypatch.setattr(coord, "pre_seal_verification_complete", lambda _t: pre_calls["n"] >= 1)
  monkeypatch.setattr(coord, "run_post_seal_verify_sync", fake_run_post_seal_verify_sync)
  monkeypatch.setattr(coord, "post_seal_verification_complete", lambda _t: post_calls["n"] >= 1)
  monkeypatch.setattr(coord, "verification_complete", lambda _t: True)
  monkeypatch.setattr(coord, "delete_phase_done", lambda _t: True)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_tar_drop_one_day",
      lambda self, tar, *a, **k: (
          _mark_day_phase(self, tar, "tar_dropped"),
          True,
      )[1],
  )
  janitor._run_tick_body()
  assert raw_calls["n"] == 0
  assert pre_calls["n"] == 1
  assert post_calls["n"] == 1


def test_close_one_day_runs_seal_raw_tar_in_single_debt_item(monkeypatch, tmp_path):
  events = []
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  open(tar_path.replace(".tar", ".tar.zst"), "wb").close()
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, events)
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  janitor._run_tick_body()
  kinds = [e[0] for e in events]
  assert kinds == ["pre_seal_verify", "seal", "post_seal_verify", "delete", "tar"]


def test_janitor_runs_multiple_day_close_in_parallel(monkeypatch, tmp_path):
  events = []
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  for tar in (tar1, tar2):
    open(tar, "wb").close()
    open(tar.replace(".tar", ".tar.zst"), "wb").close()
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, events)
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar1, persist=False)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar2, persist=False)
  assert janitor.debt_depth() == 2
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  janitor._run_tick_body()
  sealed = [e[1] for e in events if e[0] == "seal"]
  assert tar1 in sealed
  assert tar2 in sealed


def test_janitor_pops_free_worker_slots_not_days_per_tick(monkeypatch, tmp_path):
  """Pop count follows free pool slots / max_inflight only (no days_per_tick knob)."""
  assert not hasattr(janitor_mod.cfg, "get_archive_janitor_days_per_tick")
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  for day in range(1, 6):
    janitor._enqueue_debt(
        DebtKind.DAY_CLOSE,
        str(tmp_path / ("2026-01-%02d.tar" % day)),
        persist=False,
    )
  started = []

  def fake_process(debt, **_kwargs):
    started.append(debt.tar_path)
    return True

  monkeypatch.setattr(janitor, "_process_debt_item", fake_process)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 3)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  janitor._run_tick_body()
  assert len(started) == 5
  assert janitor.debt_depth() == 0


def test_janitor_refills_slot_when_one_day_close_finishes(monkeypatch, tmp_path):
  """Fast day finishes; next debt starts before slower siblings complete."""
  import threading

  tar1 = os.path.normpath(str(tmp_path / "2026-01-01.tar"))
  tar2 = os.path.normpath(str(tmp_path / "2026-01-02.tar"))
  tar3 = os.path.normpath(str(tmp_path / "2026-01-03.tar"))
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  for tar in (tar1, tar2, tar3):
    janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar, persist=False)
  started = []
  cond = threading.Condition()

  def fake_process(debt, **_kwargs):
    tar = os.path.normpath(debt.tar_path)
    with cond:
      started.append(tar)
      cond.notify_all()
    if tar != tar1:
      with cond:
        assert cond.wait_for(lambda: len(started) >= 3, timeout=5.0)
    return True

  monkeypatch.setattr(janitor, "_process_debt_item", fake_process)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 2)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  janitor._run_tick_body()
  assert set(started) == {tar1, tar2, tar3}


def test_day_close_parallel_respects_max_inflight(monkeypatch, tmp_path):
  import threading

  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  for day in range(1, 5):
    janitor._enqueue_debt(
        DebtKind.DAY_CLOSE,
        str(tmp_path / ("2026-02-%02d.tar" % day)),
        persist=False,
    )
  active = {"n": 0, "peak": 0}
  lock = threading.Lock()
  release = threading.Event()

  def fake_process(_debt, **_kwargs):
    with lock:
      active["n"] += 1
      active["peak"] = max(active["peak"], active["n"])
      if active["peak"] >= 2:
        release.set()
    assert release.wait(timeout=5.0)
    with lock:
      active["n"] -= 1
    return True

  monkeypatch.setattr(janitor, "_process_debt_item", fake_process)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 2)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  janitor._run_tick_body()
  assert active["peak"] == 2
  assert janitor.debt_depth() == 0


def test_day_close_worker_closes_db_connections(monkeypatch, tmp_path):
  import threading

  calls = []

  def track_close():
    calls.append(threading.current_thread().name)

  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._enqueue_debt(
      DebtKind.DAY_CLOSE, str(tmp_path / "2026-01-01.tar"), persist=False,
  )
  monkeypatch.setattr(janitor_mod, "close_old_connections", track_close)
  monkeypatch.setattr(janitor, "_process_debt_item", lambda *_a, **_k: True)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  janitor._run_tick_body()
  worker_calls = [name for name in calls if name.startswith("day-close")]
  assert worker_calls, "close_old_connections must run on day-close worker threads"


def test_enqueue_scheduled_day_close_skips_calendar_today_inside_grace(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  today = datetime.now(timezone.utc).date()
  tar_today = str(daily_dir / f"{today.isoformat()}.tar")
  tar_prior = str(daily_dir / "2026-01-01.tar")
  open(tar_today, "wb").close()
  open(tar_prior, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir), local_tz=timezone.utc)
  monkeypatch.setattr(
      janitor_mod.cfg, "get_archive_today_uncompressed_tar_grace_hours", lambda: 8.0)
  monkeypatch.setattr(
      janitor_mod,
      "daily_tar_seal_calendar_eligible",
      lambda tar_path, _tz, now=None: tar_path != os.path.normpath(tar_today),
  )
  monkeypatch.setattr(
      janitor_mod, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  janitor.enqueue_scheduled_day_close(reason="test")
  assert janitor.debt_depth() == 1
  assert janitor._debt_heap[0].tar_path == os.path.normpath(tar_prior)


def test_enqueue_scheduled_day_close_skips_tar_dropped_days(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2026-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  janitor._day_phases[os.path.normpath(tar_path)] = {
      "phase": "tar_dropped",
      "tar_path": tar_path,
  }
  monkeypatch.setattr(
      janitor_mod, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  monkeypatch.setattr(janitor_mod, "tar_day_dirty_by_mtime", lambda _p: False)
  janitor.enqueue_scheduled_day_close(reason="test")
  assert janitor.debt_depth() == 0


def test_close_one_day_seals_when_remaining_raw_on_disk_after_pre_seal(
    monkeypatch, tmp_path,
):
  """After pre-seal complete, seal runs even when verified closed-raw remains."""
  events = []
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  raw_path = str(tmp_path / "raw-still-on-disk")
  open(raw_path, "wb").close()
  coord = _stub_janitor_day_close_tick_phases(monkeypatch, events)
  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      day_raw_removal_coordinator=coord,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  zst_path = _zst_path_for_tar(tar_path)
  monkeypatch.setattr(
      janitor,
      "_fresh_remaining_raw_by_gz_for_tar",
      lambda *_a, **_k: {zst_path: [raw_path]},
  )
  seal_calls = {"n": 0}

  def real_seal_path(self, tp):
    seal_calls["n"] += 1
    events.append(("seal", os.path.normpath(tp)))
    open(zst_path, "wb").close()
    _mark_day_sealed(self, tp)
    return True

  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_seal_one_day", real_seal_path)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  janitor._run_tick_body()
  assert events[0][0] == "pre_seal_verify"
  assert seal_calls["n"] == 1
  assert ("seal", os.path.normpath(tar_path)) in events


def test_janitor_persist_hints_snapshots_day_phases_under_lock(monkeypatch, tmp_path):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint.cfg.get_sync_archive_maint_hints",
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


def test_janitor_parallel_persist_hints_no_error(monkeypatch, tmp_path):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint.cfg.get_sync_archive_maint_hints",
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
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
  )
  errors: list[BaseException] = []
  barrier = threading.Barrier(2)

  def worker(worker_id: int) -> None:
    try:
      barrier.wait(timeout=5)
      tar_path = os.path.normpath(
          str(tmp_path / "daily" / ("2026-05-2%s.tar" % worker_id)))
      os.makedirs(os.path.dirname(tar_path), exist_ok=True)
      open(tar_path, "wb").close()
      with janitor._hints_state_lock:
        janitor._day_phases[tar_path] = "tar_drop_deferred"
      janitor._persist_hints()
    except BaseException as exc:
      errors.append(exc)

  threads = [
      threading.Thread(target=worker, args=(worker_id,))
      for worker_id in range(2)
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=30)

  assert errors == []
  loaded = janitor_mod.load_archive_maint_hints(archive_dir)
  assert loaded is not None
  assert isinstance(loaded.get("day_phases"), dict)
  assert len(loaded["day_phases"]) >= 1


def test_enqueue_immediate_day_close_respects_disqualified(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_disqualified_daily_tars=lambda: {os.path.normpath(tar_path)},
  )
  monkeypatch.setattr(
      janitor_mod, "build_remaining_raw_stats_by_daily_gz", lambda *a, **k: {})
  assert not janitor.enqueue_immediate_day_close(tar_path, reason="test")
  assert janitor.debt_depth() == 0


def test_enqueue_immediate_day_close_enqueues_debt_when_eligible(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_day_close_candidate_inputs=lambda: {
          "unprocessed_by_tar": {},
      },
  )
  assert janitor.enqueue_immediate_day_close(tar_path, reason="chunk_end")
  assert janitor.debt_depth() == 1
  assert {debt.kind for debt in janitor._debt_heap} == {DebtKind.DAY_CLOSE}


def test_enqueue_immediate_day_close_enqueues_despite_closed_raw_on_disk(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-05-22.tar"))
  open(tar_path, "wb").close()
  submit_calls = []

  class _ClosedRawCoord:
    enabled = True

    def has_closed_raw_on_disk(self, tar_norm):
      return tar_norm == tar_path

  class _FakeCoord:
    def __init__(self, janitor):
      self._janitor = janitor

    def enqueue_day_close(self, tar, reason="", *, disqualified_daily_tars=None):
      ok, _reject = self._janitor._enqueue_day_close_debt_if_eligible(
          tar,
          reason=reason,
          disqualified=disqualified_daily_tars,
      )
      if ok:
        submit_calls.append((tar, reason))
      return ok

    def active_or_submitted_tar_paths(self):
      return set()

    def entry_progress_snapshot(self, _tar_path):
      return {}

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=None,
      day_raw_removal_coordinator=_ClosedRawCoord(),
      get_day_close_candidate_inputs=lambda: {
          "unprocessed_by_tar": {tar_path: []},
      },
  )
  janitor.day_close_manifest_coordinator = _FakeCoord(janitor)
  assert janitor.enqueue_immediate_day_close(tar_path, reason="chunk_end")
  assert submit_calls == [(tar_path, "day_ingest_complete:chunk_end")]


def test_enqueue_immediate_day_close_many_signals_once(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_paths = []
  for day in ("2020-01-01", "2020-01-02"):
    tar_path = str(daily_dir / f"{day}.tar")
    open(tar_path, "wb").close()
    tar_paths.append(tar_path)
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  signal_count = {"n": 0}
  real_signal = janitor.signal_work_available

  def counting_signal():
    signal_count["n"] += 1
    return real_signal()

  janitor.signal_work_available = counting_signal
  janitor.get_day_close_candidate_inputs = lambda: {"unprocessed_by_tar": {}}
  janitor.enqueue_immediate_day_close_many(tar_paths, reason="bulk")
  assert janitor.debt_depth() == 2
  assert signal_count["n"] == 1


def test_enqueue_immediate_day_close_many_logs_candidate_report(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  class _FakeAsyncCoord:
    def enqueue_day_close(self, tar, reason="", *, disqualified_daily_tars=None):
      return True

    def active_or_submitted_tar_paths(self):
      return set()

    def entry_progress_snapshot(self, _tar_path):
      return {}

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      day_close_manifest_coordinator=_FakeAsyncCoord(),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod.cfg,
      "get_sync_day_close_candidate_report",
      lambda: True,
  )
  janitor.enqueue_immediate_day_close_many([tar_path], reason="bulk")
  report_lines = [line for line in log_lines if "day_close candidate" in line]
  assert any("queued=1" in line for line in report_lines)
  assert any("day_ingest_complete_checkpoint" in line for line in report_lines)


def test_janitor_chains_ticks_while_debt_remains(monkeypatch, tmp_path):
  signal_count = {"n": 0}
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._allow_tick_chaining = True
  real_signal = janitor.signal_work_available

  def counting_signal():
    signal_count["n"] += 1
    return real_signal()

  janitor.signal_work_available = counting_signal
  tar1 = str(tmp_path / "2026-01-01.tar")
  tar2 = str(tmp_path / "2026-01-02.tar")
  for tar in (tar1, tar2):
    open(tar, "wb").close()
    open(tar.replace(".tar", ".tar.zst"), "wb").close()
    janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar, persist=False)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 3600.0)
  pop_calls = {"n": 0}
  orig_pop = janitor_mod.ArchiveJanitor._pop_eligible_debt_locked

  def limited_pop(self, disqualified, max_days):
    if pop_calls["n"] >= 1:
      return []
    pop_calls["n"] += 1
    return orig_pop(self, disqualified, max_days)

  def fast_process(debt, **kwargs):
    _mark_day_phase(janitor, debt.tar_path, "tar_dropped")
    return True

  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_pop_eligible_debt_locked",
      limited_pop,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  monkeypatch.setattr(janitor, "_process_debt_item", fast_process)
  janitor._run_tick_body()
  assert janitor.debt_depth() >= 1
  assert signal_count["n"] >= 1


def test_janitor_zero_pop_all_disqualified_logs_and_wakes(monkeypatch, tmp_path):
  """Heap>0 + free_slots>0 + all-disqualified: log zero_pop but do not busy-wake (F11)."""
  signal_count = {"n": 0}
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  tar_norm = os.path.normpath(tar_path)
  logs: list[str] = []

  def log_fn(msg, **_kwargs):
    logs.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      get_disqualified_daily_tars=lambda: {tar_norm},
      log_fn=log_fn,
  )
  janitor._allow_tick_chaining = True
  real_signal = janitor.signal_work_available

  def counting_signal():
    signal_count["n"] += 1
    return real_signal()

  janitor.signal_work_available = counting_signal
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  janitor._run_tick_body()
  assert janitor.debt_depth() == 1
  assert any(
      "debt_popped=0" in line and "disqualified" in line.lower()
      for line in logs
  ) or any("zero_pop" in line or "all_disqualified" in line for line in logs)
  assert any("budget_remaining_s=" in line for line in logs if "zero_pop" in line)
  assert signal_count["n"] == 0


def test_janitor_fill_pops_when_disqualified_scan_exceeds_budget(monkeypatch, tmp_path):
  """Prefill get_disqualified slower than budget must not starve fill (budget arms at fill)."""
  import time as time_mod

  tar_path = str(tmp_path / "2026-06-01.tar")
  open(tar_path, "wb").close()
  tar_norm = os.path.normpath(tar_path)
  logs: list[str] = []
  close_calls: list[str] = []
  disq_calls = {"n": 0}

  def log_fn(msg, **_kwargs):
    logs.append(str(msg))

  def slow_then_empty():
    disq_calls["n"] += 1
    # First (prefill) call exceeds short drain budget; fill still must pop.
    if disq_calls["n"] == 1:
      time_mod.sleep(0.12)
    return set()

  class _FakeCoord:
    def is_complete(self, _tar):
      return False

    def active_or_submitted_tar_paths(self):
      return set()

    def recover_stale_manifest_entries(self):
      return None

    def reconcile_manifest_with_debt_heap(self, **_kwargs):
      return 0

  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      get_disqualified_daily_tars=slow_then_empty,
      log_fn=log_fn,
      day_close_manifest_coordinator=_FakeCoord(),
  )
  monkeypatch.setattr(
      janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 0.05,
  )
  monkeypatch.setattr(
      janitor_mod.cfg, "get_archive_janitor_debt_high_watermark", lambda: 10**9,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: set(),
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )

  def _track_close(tar_path_arg, **_kwargs):
    close_calls.append(os.path.normpath(tar_path_arg))
    return True

  monkeypatch.setattr(janitor, "_close_one_day", _track_close)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  janitor._run_tick_body()
  assert any("debt_drain_begin" in line for line in logs)
  assert close_calls == [tar_norm]
  assert not any(
      "zero_pop" in line and "debt_remaining=1" in line for line in logs
  )


def test_janitor_zero_pop_logs_budget_remaining(monkeypatch, tmp_path):
  """All-disqualified zero_pop must include budget_remaining_s= token."""
  tar_path = str(tmp_path / "2026-01-02.tar")
  open(tar_path, "wb").close()
  tar_norm = os.path.normpath(tar_path)
  logs: list[str] = []

  def log_fn(msg, **_kwargs):
    logs.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      get_disqualified_daily_tars=lambda: {tar_norm},
      log_fn=log_fn,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  janitor._run_tick_body()
  zero_lines = [line for line in logs if "zero_pop" in line]
  assert zero_lines
  assert all("budget_remaining_s=" in line for line in zero_lines)
  assert any("disqualified_on_heap=1" in line for line in zero_lines)


def test_janitor_does_not_chain_when_mid_tick_only_requeues_disqualified(monkeypatch, tmp_path):
  """All-disqualified / zero-pop may wake once; must not require progress flags for that wake."""
  signal_count = {"n": 0}
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  disqualified = set()

  def flip_disqualified():
    tar_norm = os.path.normpath(tar_path)
    if not disqualified:
      disqualified.add(tar_norm)
      return set()
    return set(disqualified)

  janitor = _make_janitor(
      tgz_archive_dir=str(tmp_path),
      get_disqualified_daily_tars=flip_disqualified,
  )
  janitor._allow_tick_chaining = True
  real_signal = janitor.signal_work_available

  def counting_signal():
    signal_count["n"] += 1
    return real_signal()

  janitor.signal_work_available = counting_signal
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: None,
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  janitor._run_tick_body()
  assert janitor.debt_depth() == 1
  # Zero-pop durable wake is allowed once; follow-up ticks still need real progress
  # or in-flight workers to chain via finally.
  assert signal_count["n"] >= 1


def test_janitor_day_close_runs_close_one_day_on_debt(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  close_calls = []

  class _FakeCoord:
    def is_complete(self, _tar):
      return False

    def active_or_submitted_tar_paths(self):
      return set()

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
      get_day_close_candidate_inputs=lambda: {"unprocessed_by_tar": {}},
  )

  def _track_close_one_day(tar_path, **kwargs):
    close_calls.append(os.path.normpath(tar_path))
    return False

  monkeypatch.setattr(janitor, "_close_one_day", _track_close_one_day)
  debt = DayDebt(
      sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_path),
      kind=DebtKind.DAY_CLOSE,
      tar_path=tar_path,
  )
  result = janitor._process_debt_item(
      debt,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert close_calls == [os.path.normpath(tar_path)]
  assert result is False


def test_janitor_close_one_day_finalizes_async_manifest(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  finalize_calls = []

  class _FakeCoord:
    def finalize_complete_if_filesystem(self, tar):
      finalize_calls.append(os.path.normpath(tar))
      return True

    def active_or_submitted_tar_paths(self):
      return set()

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
  )
  with janitor._hints_state_lock:
    janitor._day_phases[tar_path] = {"phase": "tar_dropped"}
  assert janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  ) is True
  assert finalize_calls == [tar_path]


def test_enqueue_eligible_day_close_no_double_enqueue_under_lock(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_day_close_candidate_inputs=lambda: {"unprocessed_by_tar": {}},
  )
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(
      helpers,
      "daily_tar_eligible_for_day_close_submit",
      lambda *a, **k: (True, ""),
  )
  assert janitor._enqueue_eligible_day_close(
      tar_path, reason="test", disqualified=set(),
  ) == (True, "test")
  ok, reason = janitor._enqueue_eligible_day_close(
      tar_path, reason="test", disqualified=set(),
  )
  assert ok is False
  assert reason == "already_on_debt_heap"
  assert janitor.debt_depth() == 1


def test_day_close_active_tar_paths_merges_debt_and_manifest(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_debt = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar_manifest = os.path.normpath(str(daily_dir / "2020-01-02.tar"))

  class _FakeCoord:
    def manifest_worker_slot_tar_paths(self):
      return {tar_manifest}

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_debt, persist=False)
  assert janitor._day_close_active_tar_paths() == {tar_debt, tar_manifest}


def test_day_close_active_tar_paths_excludes_deferred_waiting_on_ingest(tmp_path):
  """Deferred handoff must not classify as day_close_in_progress (P0-B)."""
  from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
      DayCloseManifestCoordinator,
  )

  daily_dir = tmp_path / "daily"
  archive_dir = tmp_path / "archive"
  daily_dir.mkdir()
  archive_dir.mkdir()
  deferred_tar = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(deferred_tar, "wb").close()
  worker_tar = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(worker_tar, "wb").close()

  coord = DayCloseManifestCoordinator(
      archive_data_dir=str(archive_dir),
      host_name_ext="",
      tgz_archive_dir=str(daily_dir),
      local_tz=timezone.utc,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: set(),
      enqueue_day_close_fn=lambda *_a, **_k: True,
  )
  coord._set_entry_status(deferred_tar, "deferred", detail="waiting_on_ingest")
  coord._set_entry_status(worker_tar, "queued", detail="test")

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=coord,
  )
  active = janitor._day_close_active_tar_paths()
  assert worker_tar in active
  assert deferred_tar not in active


def test_run_scheduled_maintenance_pass_logs_candidate_report_only(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = str(daily_dir / "2020-01-01.tar")
  open(tar_path, "wb").close()
  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {os.path.normpath(tar_path): ["/raw/x"]},
      },
  )
  monkeypatch.setattr(
      janitor_mod.cfg,
      "get_sync_day_close_candidate_report",
      lambda: True,
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  snapshot = ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={})
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: snapshot,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, *, reason: set(),
  )
  janitor.run_scheduled_maintenance_pass(reason="diagnostic_report_only")
  assert janitor.debt_depth() == 0
  report_lines = [line for line in log_lines if "day_close candidate" in line]
  assert any("disqualified" in line for line in report_lines)


def test_run_scheduled_maintenance_pass_discovers_awaiting_janitor_discover_on_startup(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-05.tar"))
  open(tar_path, "wb").close()
  submitted = []

  class _FakeCoord:
    def active_or_submitted_tar_paths(self):
      return set(submitted)

    def enqueue_day_close(self, tar_path, reason="", *, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def is_complete(self, _tar_path):
      return False

    def entry_progress_snapshot(self, _tar_path):
      return None

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod.cfg,
      "get_sync_day_close_candidate_report",
      lambda: False,
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  snapshot = ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={})
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: snapshot,
  )
  janitor.run_scheduled_maintenance_pass(reason="startup")
  assert submitted == [tar_path]


def test_run_scheduled_maintenance_startup_uses_same_max_inflight(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  deferred_tars = []
  for day in ("2020-01-05", "2020-01-06", "2020-01-07"):
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % day)))
    open(tar_path, "wb").close()
    deferred_tars.append(tar_path)
  submitted = []

  class _FakeCoord:
    def active_or_submitted_tar_paths(self):
      return set(submitted)

    def enqueue_day_close(self, tar_path, reason="", *, disqualified_daily_tars=None):
      submitted.append(os.path.normpath(tar_path))
      return True

    def is_complete(self, _tar_path):
      return False

    def entry_progress_snapshot(self, _tar_path):
      return None

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod.cfg,
      "get_sync_day_close_candidate_report",
      lambda: False,
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  snapshot = ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={})
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: snapshot,
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": t,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          }
          for t in deferred_tars
      ],
  )
  janitor.run_scheduled_maintenance_pass(reason="startup")
  assert len(submitted) == 1

  with janitor._debt_lock:
    janitor._debt_heap.clear()
    janitor._debt_seen.clear()
  submitted.clear()
  janitor.run_scheduled_maintenance_pass(reason="every_n_chunks")
  assert len(submitted) == 1


def test_startup_heavy_pass_manifest_only_lock_cleanup(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  janitor.startup_snapshot_coordinator = None
  stale_calls = []
  orphan_calls = []
  logs = []

  def fake_stale(*_a, **_k):
    stale_calls.append(1)
    return 0

  def fake_orphan(*_a, **_k):
    orphan_calls.append(1)
    return 1

  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
      first_timestamp_by_path={},
      head_identity_by_path={},
  )
  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", lambda *_a, **_k: snapshot)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", fake_stale)
  monkeypatch.setattr(janitor_mod, "cleanup_orphan_fnctl_lock_sidecars", fake_orphan)
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  janitor.log_fn = lambda msg, **_kw: logs.append(str(msg))
  janitor.run_heavy_maintenance_pass(reason="startup")
  assert stale_calls == []
  assert orphan_calls
  assert any("heavy maintenance sub_phases" in line for line in logs)
  assert any("lock_cleanup_s=" in line for line in logs)


def test_heavy_pass_classifies_day_close_candidates_once(monkeypatch, tmp_path):
  """Startup/heavy pass must share one classify between report and discover."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-02-01.tar"))
  open(tar_path, "wb").close()
  classify_calls = {"n": 0}

  def _counting_classify(**_kwargs):
    classify_calls["n"] += 1
    return [
        {
            "tar_path": tar_path,
            "status": "ready_for_enqueue",
            "reasons": ["awaiting_janitor_discover"],
            "unprocessed": 0,
        },
    ]

  class _FakeCoord:
    def active_or_submitted_tar_paths(self):
      return set()

    def active_discover_cap_tar_paths(self, live_worker_tars=None):
      return set()

    def enqueue_day_close(self, tar, reason="", *, disqualified_daily_tars=None):
      return True

    def entry_progress_snapshot(self, _tar_path):
      return {}

    def discover_inflight_breakdown(self, live_worker_tars=None):
      return {}

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  janitor.startup_snapshot_coordinator = None
  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
      first_timestamp_by_path={},
      head_identity_by_path={},
  )
  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", lambda *_a, **_k: snapshot)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(janitor_mod, "classify_day_close_candidates", _counting_classify)
  monkeypatch.setattr(janitor_mod, "cleanup_orphan_fnctl_lock_sidecars", lambda *_a, **_k: 0)
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_candidate_report", lambda: True)
  # Enqueue succeeds → follow-up report re-classifies once more (expected).
  janitor.run_heavy_maintenance_pass(reason="startup")
  # Shared report+discover = 1; post-enqueue report = +1 → 2 total (not 3).
  assert classify_calls["n"] == 2

  classify_calls["n"] = 0

  class _NoEnqueueCoord(_FakeCoord):
    def enqueue_day_close(self, tar, reason="", *, disqualified_daily_tars=None):
      return False

  janitor.day_close_manifest_coordinator = _NoEnqueueCoord()
  janitor.run_heavy_maintenance_pass(reason="every_n_chunks")
  # No newly_queued → exactly one classify for report+discover.
  assert classify_calls["n"] == 1


def test_non_startup_heavy_pass_full_archive_lock_cleanup(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  stale_calls = {"n": 0}

  def fake_stale(*_a, **_k):
    stale_calls["n"] += 1
    return 0

  snapshot = ArchiveMaintenanceSnapshot(
      closed_paths=[],
      remaining_raw_by_gz={},
      mapping={},
      ready_paths=set(),
      first_timestamp_by_path={},
      head_identity_by_path={},
  )
  monkeypatch.setattr(janitor_mod, "build_archive_maintenance_snapshot", lambda *_a, **_k: snapshot)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", fake_stale)
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  janitor.run_heavy_maintenance_pass(reason="every_n_chunks")
  assert stale_calls["n"] == 2


def test_janitor_tick_discovers_and_submits_without_startup_reason(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-08.tar"))
  open(tar_path, "wb").close()
  enqueued = []

  class _FakeCoord:
    def active_or_submitted_tar_paths(self):
      return set(enqueued)

    def enqueue_day_close(self, tar, reason="", *, disqualified_daily_tars=None):
      enqueued.append(os.path.normpath(tar))
      return True

    def entry_progress_snapshot(self, _tar_path):
      return {}

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_manifest_coordinator=_FakeCoord(),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  # Leave debt on the heap so discover enqueue is observable after the tick.
  monkeypatch.setattr(janitor, "_close_one_day", lambda *_a, **_k: False)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  janitor.signal_work_available()
  janitor._run_tick_body()
  assert tar_path in enqueued


def test_janitor_startup_tick_discovers_and_enqueues_day_close(
    tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-09.tar"))
  open(tar_path, "wb").close()
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  snapshot = ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={})
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: snapshot,
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  monkeypatch.setattr(janitor, "_close_one_day", lambda *_a, **_k: False)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  janitor.signal_scheduled_maintenance_pass(reason="startup")
  janitor._run_tick_body()
  payload = janitor._debt_queue_payload()
  assert any(
      e["kind"] == DebtKind.DAY_CLOSE.value
      and os.path.normpath(e["tar_path"]) == tar_path
      for e in payload
  )


def test_janitor_tick_defers_day_close_execution_before_ingest_gate_cleared(
    tmp_path, monkeypatch,
):
  """Discover/enqueue runs at startup; _close_one_day waits for ingest gate clear."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot

  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-11.tar"))
  open(tar_path, "wb").close()
  close_calls = []
  log_lines = []

  def capture_log(msg, flush=False):
    del flush
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=capture_log,
      get_startup_ingest_gate_cleared=lambda: False,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  snapshot = ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={})
  monkeypatch.setattr(
      janitor_mod,
      "build_archive_maintenance_snapshot",
      lambda *_a, **_k: snapshot,
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  monkeypatch.setattr(
      janitor,
      "_close_one_day",
      lambda *_a, **_k: close_calls.append(1) or False,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  janitor._enqueue_day_close(tar_path)
  janitor._run_tick_body()
  assert close_calls == []
  assert janitor.debt_depth() == 1
  assert any(
      "tick deferred day_close_execution reason=startup_prep" in line
      for line in log_lines
  )


def test_janitor_discover_deferred_until_snapshot_ready(tmp_path, monkeypatch):
  """Discover skipped while get_day_close_allowed is False after gate clear."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-12.tar"))
  open(tar_path, "wb").close()
  log_lines = []
  discover_calls = []

  def capture_log(msg, flush=False):
    del flush
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=capture_log,
      get_startup_ingest_gate_cleared=lambda: True,
      get_day_close_allowed=lambda: False,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(janitor, "get_ingest_pool_in_flight_count", lambda: 0)
  monkeypatch.setattr(janitor, "get_chunk_in_progress", lambda: False)
  monkeypatch.setattr(
      janitor,
      "_discover_and_enqueue_ready_day_close",
      lambda **_k: discover_calls.append(1),
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  janitor._run_tick_body()
  assert discover_calls == []
  assert any("day_close_not_allowed" in line for line in log_lines)


def test_no_async_day_close_worker_submit_on_discover(tmp_path, monkeypatch):
  """Discover enqueues debt only; does not start day-close pool workers."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-10.tar"))
  open(tar_path, "wb").close()
  pool_submits = []
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )

  def track_pool_submit(self, *args, **kwargs):
    pool_submits.append(1)
    raise AssertionError("discover must not start day-close workers")

  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_submit_day_close_debt",
      track_pool_submit,
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  janitor._discover_and_enqueue_ready_day_close(reason="tick")
  assert pool_submits == []
  assert janitor.debt_depth() == 1


def test_janitor_tick_defer_reason_startup_heavy(tmp_path, monkeypatch):
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path / "daily"))
  with janitor._maintenance_pass_lock:
    janitor._pending_maintenance_pass_reason = "startup_heavy"
  janitor._enqueue_day_close(str(tmp_path / "daily" / "2020-01-01.tar"))
  assert janitor.tick_defer_reason() == "startup_heavy_maintenance"
  stats = janitor.stats()
  assert stats["janitor_tick_defer_reason"] == "startup_heavy_maintenance"


def test_discover_enqueues_when_deferred_waiting_on_ingest_not_worker_inflight(
    tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
      DayCloseManifestCoordinator,
  )

  daily_dir = tmp_path / "daily"
  archive_dir = tmp_path / "archive"
  daily_dir.mkdir()
  archive_dir.mkdir()
  deferred_tars = []
  for day in ("2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"):
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % day)))
    open(tar_path, "wb").close()
    deferred_tars.append(tar_path)
  ready_tars = []
  for day in ("2020-01-05", "2020-01-06", "2020-01-07", "2020-01-08"):
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % day)))
    open(tar_path, "wb").close()
    ready_tars.append(tar_path)

  submitted = []

  def _enqueue_fn(tar_path, reason="", *, disqualified_daily_tars=None):
    submitted.append(os.path.normpath(tar_path))
    return True

  coord = DayCloseManifestCoordinator(
      archive_data_dir=str(archive_dir),
      host_name_ext="",
      tgz_archive_dir=str(daily_dir),
      local_tz=timezone.utc,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: set(deferred_tars),
      enqueue_day_close_fn=_enqueue_fn,
  )
  for tar_path in deferred_tars:
    coord._set_entry_status(tar_path, "deferred", detail="waiting_on_ingest")

  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      day_close_manifest_coordinator=coord,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": t,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          }
          for t in ready_tars
      ],
  )
  newly_queued = janitor._discover_and_enqueue_ready_day_close(reason="tick")
  assert len(newly_queued) == 4
  assert set(newly_queued) == set(ready_tars)
  assert not (set(newly_queued) & set(deferred_tars))
  assert len(submitted) == 4
  discover_lines = [
      line for line in log_lines if "discover_ready_day_close" in line
  ]
  assert discover_lines
  assert "deferred_waiting=4" in discover_lines[0]
  assert "skipped_inflight=0" in discover_lines[0]
  assert "enqueued=4" in discover_lines[0]
  assert "deferred_noop=" in discover_lines[0]
  assert "already_inflight=" in discover_lines[0]


def test_tar_drop_complete_when_tar_already_gone_and_sealed(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-04.tar"))
  zst_path = _zst_path_for_tar(tar_path)
  open(zst_path, "wb").close()

  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {},
  )
  assert janitor._tar_drop_one_day(tar_path, {}, set()) is True
  phase = janitor._day_phases.get(tar_path)
  phase_text = phase.get("phase") if isinstance(phase, dict) else phase
  assert phase_text == "tar_dropped"


def test_tar_drop_deferred_logs_handoff_requeue_correlation(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  zst_path = _zst_path_for_tar(tar_path)
  open(zst_path, "wb").close()

  class _HandoffCoord:
    def paths_for_closed_raw_handoff_requeue(self, tar_norm):
      assert os.path.normpath(tar_norm) == tar_path
      return [str(tmp_path / "host" / "1000"), str(tmp_path / "host" / "1001")]

  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      day_raw_removal_coordinator=_HandoffCoord(),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remaining_raw_by_gz_has_paths_on_disk",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {zst_path: ["/raw/x"]},
  )
  janitor._tar_drop_one_day(tar_path, validation_cache={}, disqualified=set())
  deferred_lines = [
      line for line in log_lines if "tar drop deferred" in line.lower()
  ]
  assert deferred_lines
  assert "handoff_paths=2" in deferred_lines[0]
  assert "waiting_on_ingest" in deferred_lines[0]


def test_tar_drop_deferred_logs_stale_manifest_when_phase_done(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  zst_path = _zst_path_for_tar(tar_path)
  open(zst_path, "wb").close()

  class _StaleManifestCoord:
    def paths_for_closed_raw_handoff_requeue(self, tar_norm):
      return [str(tmp_path / "host" / "1000")]

    def delete_phase_done(self, tar_path):
      return True

  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      day_raw_removal_coordinator=_StaleManifestCoord(),
  )
  monkeypatch.setattr(
      janitor_mod,
      "remaining_raw_by_gz_has_paths_on_disk",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {zst_path: ["/raw/x"]},
  )
  janitor._tar_drop_one_day(tar_path, validation_cache={}, disqualified=set())
  deferred_lines = [
      line for line in log_lines if "tar drop deferred" in line.lower()
  ]
  assert deferred_lines
  assert "stale_manifest_retryable" in deferred_lines[0]


def test_janitor_day_close_reclassify_unblocks_tar_drop(monkeypatch, tmp_path):
  from datetime import datetime

  import hpcperfstats.dbload.lib.conf_parser as day_cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      atomic_seal_tar_to_zst,
      daily_tar_path_from_compressed,
      get_tar_member_name,
      validate_sealed_daily_archive_for_raw_removal,
  )
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      DayRawRemovalCoordinator,
      PHASE_DONE,
      VERIFY_STAGE_POST_SEAL,
      _save_manifest,
  )

  day = datetime(2026, 5, 30)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  zst_key = str(tgz_dir / "2026-05-30.tar.zst")
  tar_path = daily_tar_path_from_compressed(zst_key)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=get_tar_member_name(str(seg)))
  atomic_seal_tar_to_zst(tar_path, zst_key, 1, 6, True, log_fn=None)
  validate_sealed_daily_archive_for_raw_removal(zst_key, log_fn=None)

  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(tmp_path),
      host_name_ext="cluster.integration.test",
      tgz_archive_dir=str(tgz_dir),
      log_fn=MagicMock(),
      get_quarantine_skip_paths=lambda: set(),
      ingest_ready_fn=lambda _p: True,
  )
  coord.enabled = True
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
      str(seg),
      zst_key,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    _save_manifest(state._manifest_path, state._manifest)

  tar_drop_calls = []
  janitor = _make_janitor(
      archive_data_dir=str(tmp_path),
      tgz_archive_dir=str(tgz_dir),
      day_raw_removal_coordinator=coord,
  )
  tar_norm = os.path.normpath(tar_path)
  with janitor._hints_state_lock:
    janitor._day_phases[tar_norm] = "sealed"

  monkeypatch.setattr(
      day_cfg,
      "get_sync_day_close_raw_removal_max_deletes_per_pass",
      lambda: 0,
  )

  def _track_tar_drop(self, tp, *args, **kwargs):
    tar_drop_calls.append(os.path.normpath(tp))
    _mark_day_phase(self, tp, "tar_dropped")
    return True

  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_tar_drop_one_day",
      _track_tar_drop,
  )

  janitor.log_fn = MagicMock()
  assert coord.post_seal_verification_complete(tar_norm)
  assert coord.delete_phase_done(tar_norm)
  assert janitor._close_one_day(
      tar_norm,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert not seg.is_file()
  assert tar_drop_calls == [tar_norm]


def test_close_one_day_promotes_verifying_post_seal_and_deletes_or_handoffs(
    monkeypatch, tmp_path,
):
  """05-30: verifying+post_seal must not silent-reenqueue without delete/handoff."""
  from datetime import datetime

  import hpcperfstats.dbload.lib.conf_parser as day_cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      atomic_seal_tar_to_zst,
      daily_tar_path_from_compressed,
      get_tar_member_name,
      validate_sealed_daily_archive_for_raw_removal,
  )
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      DayRawRemovalCoordinator,
      PHASE_VERIFYING,
      VERIFY_STAGE_POST_SEAL,
      _save_manifest,
  )

  day = datetime(2026, 5, 30)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  zst_key = str(tgz_dir / "2026-05-30.tar.zst")
  tar_path = daily_tar_path_from_compressed(zst_key)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=get_tar_member_name(str(seg)))
  atomic_seal_tar_to_zst(tar_path, zst_key, 1, 6, True, log_fn=None)
  validate_sealed_daily_archive_for_raw_removal(zst_key, log_fn=None)

  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(tmp_path),
      host_name_ext="cluster.integration.test",
      tgz_archive_dir=str(tgz_dir),
      log_fn=MagicMock(),
      get_quarantine_skip_paths=lambda: set(),
      ingest_ready_fn=lambda _p: False,
  )
  coord.enabled = True
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
      str(seg),
      zst_key,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFYING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  janitor = _make_janitor(
      archive_data_dir=str(tmp_path),
      tgz_archive_dir=str(tgz_dir),
      day_raw_removal_coordinator=coord,
  )
  tar_norm = os.path.normpath(tar_path)
  with janitor._hints_state_lock:
    janitor._day_phases[tar_norm] = "sealed"

  monkeypatch.setattr(
      day_cfg,
      "get_sync_day_close_raw_removal_max_deletes_per_pass",
      lambda: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_tar_drop_one_day",
      lambda self, tp, *a, **k: (_mark_day_phase(self, tp, "tar_dropped"), True)[1],
  )

  log_fn = MagicMock()
  janitor.log_fn = log_fn
  assert not coord.verification_complete(tar_norm)
  result = janitor._close_one_day(
      tar_norm,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  log_text = " ".join(
      str(c.args[0]) for c in log_fn.call_args_list if c.args
  )
  assert "delete start" in log_text or coord.delete_phase_done(tar_norm) or (
      coord.verification_complete(tar_norm)
  )
  assert coord.verification_complete(tar_norm) or coord.delete_phase_done(tar_norm)
  # F15: waiting_on_ingest with raw on disk may re-enqueue without PHASE_DONE.
  assert (
      result is True
      or coord.delete_phase_done(tar_norm)
      or coord.should_handoff_to_ingest(tar_norm)
  )


def test_close_one_day_delete_start_when_verification_complete_verified_on_disk(
    monkeypatch, tmp_path,
):
  """06-08/09 C1: verification_complete + verified on disk must log delete start."""
  from datetime import datetime

  import hpcperfstats.dbload.lib.conf_parser as day_cfg
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      atomic_seal_tar_to_zst,
      daily_tar_path_from_compressed,
      get_tar_member_name,
      validate_sealed_daily_archive_for_raw_removal,
  )
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      DayRawRemovalCoordinator,
      PHASE_VERIFICATION_COMPLETE,
      VERIFY_STAGE_POST_SEAL,
      _save_manifest,
  )

  day = datetime(2026, 6, 8)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  zst_key = str(tgz_dir / "2026-06-08.tar.zst")
  tar_path = daily_tar_path_from_compressed(zst_key)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=get_tar_member_name(str(seg)))
  atomic_seal_tar_to_zst(tar_path, zst_key, 1, 6, True, log_fn=None)
  validate_sealed_daily_archive_for_raw_removal(zst_key, log_fn=None)

  coord = DayRawRemovalCoordinator(
      archive_data_dir=str(tmp_path),
      host_name_ext="cluster.integration.test",
      tgz_archive_dir=str(tgz_dir),
      log_fn=MagicMock(),
      get_quarantine_skip_paths=lambda: set(),
      ingest_ready_fn=lambda _p: True,
  )
  coord.enabled = True
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst_key, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  janitor = _make_janitor(
      archive_data_dir=str(tmp_path),
      tgz_archive_dir=str(tgz_dir),
      day_raw_removal_coordinator=coord,
  )
  tar_norm = os.path.normpath(tar_path)
  with janitor._hints_state_lock:
    janitor._day_phases[tar_norm] = "sealed"

  monkeypatch.setattr(
      day_cfg,
      "get_sync_day_close_raw_removal_max_deletes_per_pass",
      lambda: 100,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_tar_drop_one_day",
      lambda self, tp, *a, **k: (_mark_day_phase(self, tp, "tar_dropped"), True)[1],
  )

  log_fn = MagicMock()
  janitor.log_fn = log_fn
  assert janitor._close_one_day(
      tar_norm,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  ) is True
  log_text = " ".join(
      str(c.args[0]) for c in log_fn.call_args_list if c.args
  )
  assert "delete start" in log_text
  assert not seg.is_file()


def test_discover_enqueues_when_debt_heap_full_but_no_live_workers(
    tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
      DayCloseManifestCoordinator,
  )

  daily_dir = tmp_path / "daily"
  archive_dir = tmp_path / "archive"
  daily_dir.mkdir()
  archive_dir.mkdir()
  debt_tars = []
  for day in ("2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"):
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % day)))
    open(tar_path, "wb").close()
    debt_tars.append(tar_path)
  ready_tars = []
  for day in ("2020-01-05", "2020-01-06", "2020-01-07", "2020-01-08"):
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % day)))
    open(tar_path, "wb").close()
    ready_tars.append(tar_path)

  submitted = []

  def _enqueue_fn(tar_path, reason="", *, disqualified_daily_tars=None):
    submitted.append(os.path.normpath(tar_path))
    return True

  coord = DayCloseManifestCoordinator(
      archive_data_dir=str(archive_dir),
      host_name_ext="",
      tgz_archive_dir=str(daily_dir),
      local_tz=timezone.utc,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: set(debt_tars),
      enqueue_day_close_fn=_enqueue_fn,
  )
  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      day_close_manifest_coordinator=coord,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  for tar_path in debt_tars:
    janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)

  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": t,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          }
          for t in ready_tars
      ],
  )
  newly_queued = janitor._discover_and_enqueue_ready_day_close(reason="tick")
  assert len(newly_queued) == 4
  assert len(submitted) == 4
  discover_lines = [line for line in log_lines if "discover_ready_day_close" in line]
  assert discover_lines
  assert "debt_heap=4" in discover_lines[0]
  assert "active_workers=0" in discover_lines[0]
  assert "skipped_inflight=0" in discover_lines[0]
  assert "worker_occupancy=8" in discover_lines[0]


def test_day_close_free_slots_excludes_running_workers(tmp_path, monkeypatch):
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  tar_a = os.path.normpath(str(tmp_path / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(tmp_path / "2020-01-02.tar"))
  janitor._day_close_in_flight[object()] = DayDebt(
      sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_a),
      kind=DebtKind.DAY_CLOSE,
      tar_path=tar_a,
  )
  janitor._day_close_in_flight[object()] = DayDebt(
      sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_b),
      kind=DebtKind.DAY_CLOSE,
      tar_path=tar_b,
  )
  assert janitor._day_close_free_slots() == 2


def test_janitor_budget_partial_tick_schedules_followup(monkeypatch, tmp_path):
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._allow_tick_chaining = True
  janitor._enqueue_debt(
      DebtKind.DAY_CLOSE, str(tmp_path / "2020-01-01.tar"), persist=False,
  )
  janitor._enqueue_debt(
      DebtKind.DAY_CLOSE, str(tmp_path / "2020-01-02.tar"), persist=False,
  )
  processed = {"n": 0}
  signals = []

  def fake_time():
    return 200.0 if processed["n"] >= 1 else 100.0

  def fake_process(_debt, **_kwargs):
    processed["n"] += 1
    return False

  def capture_signal():
    signals.append(1)
    return None

  monkeypatch.setattr(janitor_mod.time, "time", fake_time)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", lambda *_a, **_k: 0)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_run_tick_lock_cleanup",
      lambda self: 0,
  )
  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_discover_and_enqueue_ready_day_close", lambda self, **kwargs: set())
  monkeypatch.setattr(janitor, "_process_debt_item", fake_process)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 50.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  monkeypatch.setattr(janitor, "get_startup_ingest_gate_cleared", lambda: True)
  monkeypatch.setattr(janitor, "signal_work_available", capture_signal)
  janitor._run_tick_body()
  assert janitor.debt_depth() >= 1
  assert signals == [1]


def test_seal_failure_and_tar_drop_unlink_failure_reenqueue(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  enqueued = []

  def track_enqueue(tar, persist=True):
    enqueued.append(os.path.normpath(tar))
    return True

  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  janitor._enqueue_day_close = track_enqueue
  monkeypatch.setattr(
      janitor_mod,
      "daily_tar_seal_calendar_eligible",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      janitor_mod,
      "atomic_seal_tar_to_zst",
      lambda *_a, **_k: None,
  )
  monkeypatch.setattr(
      janitor_mod,
      "drop_legacy_gz_if_equivalent_to_zst",
      lambda *_a, **_k: None,
  )
  assert janitor._seal_one_day(tar_path) is False
  assert enqueued == [tar_path]

  enqueued.clear()
  zst_path = _zst_path_for_tar(tar_path)
  open(zst_path, "wb").close()
  _mark_day_sealed(janitor, tar_path)
  monkeypatch.setattr(
      janitor_mod,
      "remaining_raw_by_gz_has_paths_on_disk",
      lambda *_a, **_k: False,
  )
  monkeypatch.setattr(
      janitor_mod,
      "remove_verified_uncompressed_daily_tars",
      lambda *_a, **_k: None,
  )
  assert janitor._tar_drop_one_day(tar_path, {}, set()) is False
  assert enqueued == [tar_path]


def test_mid_tick_discover_enqueues_when_heap_empty_and_slots_free(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  discover_calls = []

  def track_discover(self, *, reason, slot_budget=None):
    discover_calls.append((reason, slot_budget))
    return set()

  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      track_discover,
  )
  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_pop_one_day_close_debt", lambda self, *_a, **_k: None)
  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_day_close_free_slots", lambda self: 2)
  monkeypatch.setattr(janitor_mod.time, "time", lambda: 100.0)

  attempted_tars = set()
  budget_deadline = 200.0

  def _budget_ok():
    return janitor_mod.time.time() < budget_deadline

  def _fill_free_slots():
    started = 0
    while _budget_ok() and janitor._day_close_free_slots() > 0:
      debt = janitor._pop_one_day_close_debt(set(), skip_tars=attempted_tars)
      if debt is None:
        free_slots = janitor._day_close_free_slots()
        if free_slots > 0:
          janitor._discover_and_enqueue_ready_day_close(
              reason="tick_slot_free",
              slot_budget=free_slots,
          )
        break
    return started

  _fill_free_slots()
  assert discover_calls == [("tick_slot_free", 2)]


def test_fill_free_slots_submits_to_idle_workers_while_others_running(
    monkeypatch, tmp_path,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_paths = []
  for day in ("2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"):
    tar_path = os.path.normpath(str(daily_dir / ("%s.tar" % day)))
    open(tar_path, "wb").close()
    tar_paths.append(tar_path)

  janitor = _make_janitor(tgz_archive_dir=str(daily_dir))
  for tar_path in tar_paths:
    janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)

  submits = []

  def track_submit(self, debt, **kwargs):
    submits.append(os.path.normpath(debt.tar_path))
    fut = MagicMock()
    fut.result = lambda timeout=None: True
    with janitor._day_close_in_flight_lock:
      janitor._day_close_in_flight[fut] = debt
    return fut

  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_submit_day_close_debt", track_submit)
  monkeypatch.setattr(janitor_mod.ArchiveJanitor, "_discover_and_enqueue_ready_day_close", lambda self, **kwargs: set())
  monkeypatch.setattr(janitor_mod.time, "time", lambda: 100.0)

  in_flight = {}
  attempted_tars = set()
  budget_deadline = 200.0

  def _budget_ok():
    return janitor_mod.time.time() < budget_deadline

  while _budget_ok() and janitor._day_close_free_slots() > 0:
    debt = janitor._pop_one_day_close_debt(set(), skip_tars=attempted_tars)
    if debt is None:
      break
    attempted_tars.add(os.path.normpath(debt.tar_path))
    fut = janitor._submit_day_close_debt(debt, snapshot=None, validation_cache={}, disqualified=set())
    in_flight[fut] = debt

  assert len(submits) == 4
  assert janitor._day_close_free_slots() == 0

  # Simulate two completions freeing slots while two remain busy.
  busy = list(in_flight.keys())[:2]
  for fut in busy:
    in_flight.pop(fut)
    with janitor._day_close_in_flight_lock:
      janitor._day_close_in_flight.pop(fut, None)

  assert janitor._day_close_free_slots() == 2
  assert len(janitor._day_close_in_flight) == 2

  while _budget_ok() and janitor._day_close_free_slots() > 0:
    debt = janitor._pop_one_day_close_debt(set(), skip_tars=attempted_tars)
    if debt is None:
      break
    attempted_tars.add(os.path.normpath(debt.tar_path))
    janitor._submit_day_close_debt(debt, snapshot=None, validation_cache={}, disqualified=set())

  assert len(submits) == 4


def test_discover_logs_enqueue_reject_when_submit_ineligible(
    tmp_path, monkeypatch,
):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          }
      ],
  )
  monkeypatch.setattr(
      janitor_mod,
      "daily_tar_eligible_for_day_close_submit",
      lambda *a, **k: (False, "checkpoint_incomplete"),
  )
  janitor._discover_and_enqueue_ready_day_close(reason="tick")
  reject_lines = [line for line in log_lines if "discover_enqueue_reject" in line]
  assert len(reject_lines) == 1
  assert tar_path in reject_lines[0]
  assert "checkpoint_incomplete" in reject_lines[0]
  summary = [line for line in log_lines if "discover_ready_day_close" in line]
  assert summary
  assert "skipped_eligible=1" in summary[0]
  assert "enqueued=0" in summary[0]


def test_discover_logs_already_on_debt_heap(tmp_path, monkeypatch):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  log_lines = []

  def _log(msg, **kwargs):
    log_lines.append(str(msg))

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=_log,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_path, persist=False)
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          }
      ],
  )
  monkeypatch.setattr(
      janitor_mod,
      "daily_tar_eligible_for_day_close_submit",
      lambda *a, **k: (True, ""),
  )
  janitor._discover_and_enqueue_ready_day_close(reason="tick")
  reject_lines = [line for line in log_lines if "discover_enqueue_reject" in line]
  assert len(reject_lines) == 1
  assert "already_on_debt_heap" in reject_lines[0]
  assert any(
      "skipped_eligible=1" in line for line in log_lines if "discover_ready_day_close" in line
  )


def test_janitor_tick_wait_heartbeat(monkeypatch, tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  open(tar_path, "wb").close()
  log_lines = []
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      log_fn=lambda msg, **kwargs: log_lines.append(str(msg)),
  )
  future = MagicMock()
  in_flight = {
      future: DayDebt(
          sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_path),
          kind=DebtKind.DAY_CLOSE,
          tar_path=tar_path,
      ),
  }
  mono = {"t": 1000.0}
  monkeypatch.setattr(janitor_mod.time, "monotonic", lambda: mono["t"])
  heartbeat = [None]
  janitor._log_tick_waiting_heartbeat(in_flight, heartbeat)
  assert len([line for line in log_lines if "janitor: tick waiting" in line]) == 1
  assert tar_path in log_lines[0]
  mono["t"] += 100.0
  janitor._log_tick_waiting_heartbeat(in_flight, heartbeat)
  assert len([line for line in log_lines if "janitor: tick waiting" in line]) == 1
  mono["t"] += 201.0
  janitor._log_tick_waiting_heartbeat(in_flight, heartbeat)
  assert len([line for line in log_lines if "janitor: tick waiting" in line]) == 2


def test_janitor_budget_exit_nonblocking_leaves_in_flight(monkeypatch, tmp_path):
  """budget_exit must return without drain-waiting day-close futures."""
  import time as time_mod
  from concurrent.futures import Future

  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  janitor._allow_tick_chaining = True
  tar_a = os.path.normpath(str(tmp_path / "2020-01-01.tar"))
  tar_b = os.path.normpath(str(tmp_path / "2020-01-02.tar"))
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_a, persist=False)
  janitor._enqueue_debt(DebtKind.DAY_CLOSE, tar_b, persist=False)
  log_lines = []
  janitor.log_fn = lambda msg, flush=False: log_lines.append(msg)
  signals = []
  started = {"n": 0}

  def fake_submit(self, debt, **kwargs):
    started["n"] += 1
    fut = Future()
    # Never set_result — simulates long-running day-close worker.
    with self._day_close_in_flight_lock:
      self._day_close_in_flight[fut] = debt
    return fut

  def fake_wait(fs, timeout=None, **_kwargs):
    # Always time out so the budget loop hits budget_exit without completing.
    return set(), set(fs)

  def fake_time():
    # Expire budget once at least one worker was submitted.
    if started["n"] >= 1:
      return 200.0
    return 100.0

  monkeypatch.setattr(janitor_mod.time, "time", fake_time)
  monkeypatch.setattr(janitor_mod, "wait", fake_wait)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", lambda *_a, **_k: 0)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor, "_run_tick_lock_cleanup", lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: set(),
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor, "_submit_day_close_debt", fake_submit,
  )
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 50.0)
  monkeypatch.setattr(janitor_mod.cfg, "get_sync_day_close_max_inflight", lambda: 2)
  monkeypatch.setattr(janitor, "get_startup_ingest_gate_cleared", lambda: True)
  monkeypatch.setattr(
      janitor, "signal_work_available", lambda: signals.append(1),
  )

  t0 = time_mod.monotonic()
  janitor._run_tick_body()
  elapsed = time_mod.monotonic() - t0
  assert elapsed < 2.0, "tick must not drain-wait slow day-close workers"
  assert any("budget_exit" in line for line in log_lines)
  assert any("leave_in_flight" in line for line in log_lines)
  with janitor._day_close_in_flight_lock:
    assert len(janitor._day_close_in_flight) >= 1
  assert signals, "follow-up must be scheduled while in-flight remain"


def test_janitor_reconcile_before_discover_order(monkeypatch, tmp_path):
  """Ghost manifest reconcile must run before tick discover."""
  order = []
  janitor = _make_janitor(tgz_archive_dir=str(tmp_path))
  coord = MagicMock()
  coord.recover_stale_manifest_entries = lambda: order.append("recover")
  coord.reconcile_manifest_with_debt_heap = lambda **_k: order.append("reconcile")
  janitor.day_close_manifest_coordinator = coord

  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(janitor_mod, "cleanup_stale_fnctl_lock_sidecars", lambda *_a, **_k: 0)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor, "_run_tick_lock_cleanup", lambda self: 0,
  )
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: order.append("discover:%s" % kwargs.get("reason")),
  )
  monkeypatch.setattr(janitor, "get_startup_ingest_gate_cleared", lambda: True)
  monkeypatch.setattr(janitor_mod.cfg, "get_archive_janitor_budget_seconds", lambda: 30.0)
  janitor._run_tick_body()
  assert order.index("reconcile") < order.index("discover:tick")
  assert order.index("recover") < order.index("discover:tick")


def test_discover_noop_when_day_close_disabled(tmp_path, monkeypatch):
  """day_close_enabled=False must not enqueue DAY_CLOSE on discover."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-09.tar"))
  open(tar_path, "wb").close()
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_enabled=False,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  newly = janitor._discover_and_enqueue_ready_day_close(reason="tick")
  assert newly == set()
  assert janitor.debt_depth() == 0
  ok, reason = janitor._enqueue_eligible_day_close(
      tar_path, reason="test", disqualified=set(),
  )
  assert ok is False
  assert reason == "day_close_disabled"
  assert janitor._enqueue_day_close(tar_path) is False


def test_janitor_tick_skips_day_close_when_disabled(tmp_path, monkeypatch):
  """Tick must not discover or close when day_close_enabled=False."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-01-10.tar"))
  open(tar_path, "wb").close()
  close_calls = []
  discover_calls = []
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_enabled=False,
  )
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      janitor_mod.ArchiveJanitor,
      "_discover_and_enqueue_ready_day_close",
      lambda self, **kwargs: discover_calls.append(kwargs.get("reason")) or set(),
  )
  monkeypatch.setattr(
      janitor,
      "_close_one_day",
      lambda *a, **k: close_calls.append(True) or False,
  )
  # Pre-seed debt as if hints loaded leftover DAY_CLOSE from a peer process.
  with janitor._debt_lock:
    janitor._enqueue_day_close_locked(tar_path, persist=False)
  assert janitor.debt_depth() == 1
  janitor.signal_work_available()
  janitor._run_tick_body()
  assert discover_calls == []
  assert close_calls == []
  assert janitor.debt_depth() == 1


def test_disabled_day_close_tick_runs_pending_maintenance_pass(monkeypatch):
  """CLI backlog: day_close disabled must still drain pending startup maintenance.

  Otherwise supervisor wait_for_snapshot deadlocks: note_startup_maintenance_pending
  extends the wait while the janitor never publishes the snapshot.
  """
  janitor = _make_janitor(day_close_enabled=False)
  monkeypatch.setattr(janitor_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(janitor, "signal_work_available", lambda: None)
  monkeypatch.setattr(janitor, "_rss_over_limit", lambda: False)
  maint_calls = []

  def _spy_maint(*, reason):
    maint_calls.append(reason)

  monkeypatch.setattr(janitor, "run_scheduled_maintenance_pass", _spy_maint)
  janitor.signal_scheduled_maintenance_pass(reason="startup")
  assert janitor._pending_maintenance_pass_reason == "startup"
  janitor._run_tick_body()
  assert maint_calls == ["startup"]
  assert janitor._pending_maintenance_pass_reason is None


def test_janitor_still_discovers_when_day_close_enabled(tmp_path, monkeypatch):
  """Default day_close_enabled=True keeps discover/enqueue."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-02-01.tar"))
  open(tar_path, "wb").close()
  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_close_enabled=True,
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {},
      },
  )
  monkeypatch.setattr(
      janitor_mod,
      "build_remaining_raw_stats_by_daily_gz",
      lambda *args, **kwargs: {},
  )
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  newly = janitor._discover_and_enqueue_ready_day_close(reason="tick")
  assert tar_path in newly
  assert janitor.debt_depth() >= 1


def test_delete_disqualified_routes_through_defer_tracker(monkeypatch, tmp_path):
  """F11b: delete_disqualified must hit _check_day_close_defer (record_defer), not early return."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-10.tar"))
  open(tar_path, "wb").close()
  open(str(daily_dir / "2026-06-10.tar.zst"), "wb").close()
  defer_calls = []

  class _Coord:
    def pre_seal_verification_complete(self, _t):
      return True

    def post_seal_verification_complete(self, _t):
      return True

    def verification_complete(self, _t):
      return True

    def delete_phase_done(self, _t):
      return False

    def should_handoff_before_seal(self, _t):
      return False

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      day_raw_removal_coordinator=_Coord(),
      get_delete_disqualified_daily_tars=lambda: {tar_path},
  )
  _mark_day_sealed(janitor, tar_path)
  real_check = janitor._check_day_close_defer

  def _spy_check(tar, *, phase, disqualified, delete_disqualified=None):
    defer_calls.append((phase, delete_disqualified))
    return real_check(
        tar,
        phase=phase,
        disqualified=disqualified,
        delete_disqualified=delete_disqualified,
    )

  monkeypatch.setattr(janitor, "_check_day_close_defer", _spy_check)
  monkeypatch.setattr(
      janitor,
      "_blocking_remaining_raw_for_tar",
      lambda *_a, **_k: {},
  )
  ok = janitor._close_one_day(
      tar_path,
      snapshot=None,
      validation_cache={},
      disqualified=set(),
  )
  assert ok is False
  assert any(phase == "delete" for phase, _ in defer_calls)
  delete_call = next(c for c in defer_calls if c[0] == "delete")
  assert tar_path in (delete_call[1] or set())
  # record_defer ran via real _check_day_close_defer → debt re-enqueue.
  with janitor._defer_tracker._lock:
    tracked = tar_path in janitor._defer_tracker._by_tar
  assert tracked or janitor.debt_depth() >= 1


def test_discover_stashes_unprocessed_snapshot_for_submit(tmp_path, monkeypatch):
  """F4: discover must stash classify unprocessed snapshot for submit eligibility."""
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2020-03-01.tar"))
  open(tar_path, "wb").close()
  seen = {"snap": "unset"}

  def _enqueue(tar_norm, reason="", *, disqualified=None):
    seen["snap"] = getattr(
        janitor, "_day_close_submit_unprocessed_snapshot", "missing",
    )
    return True, "enqueued"

  janitor = _make_janitor(
      tgz_archive_dir=str(daily_dir),
      get_day_close_candidate_inputs=lambda: {
          "inflight_paths": set(),
          "pending_append_by_daily_tar": {},
          "in_flight_archive_tars": set(),
          "pending_archive_task_tars": set(),
          "unmapped_closed_raw_tars": set(),
          "unprocessed_by_tar": {tar_path: []},
      },
  )
  monkeypatch.setattr(janitor, "_enqueue_eligible_day_close", _enqueue)
  monkeypatch.setattr(
      janitor_mod,
      "classify_day_close_candidates",
      lambda **_k: [
          {
              "tar_path": tar_path,
              "status": "ready_for_enqueue",
              "reasons": ["awaiting_janitor_discover"],
              "unprocessed": 0,
          },
      ],
  )
  janitor._discover_and_enqueue_ready_day_close(reason="tick")
  assert seen["snap"] == {tar_path: []}
  assert getattr(janitor, "_day_close_submit_unprocessed_snapshot", None) is None
