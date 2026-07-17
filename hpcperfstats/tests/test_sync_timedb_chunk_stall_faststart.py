"""Regression matrix R1–R28: MainThread must not full-snapshot on exclude/handoff;

async scan-ahead + known-pending supplements (chunk-stall-startup-faststart plan).
"""

from __future__ import annotations

import inspect
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    build_day_scoped_closed_raw_by_gz,
    build_remaining_raw_for_daily_tar,
    build_remaining_raw_stats_by_daily_gz,
)
from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    _save_manifest,
)
from hpcperfstats.tests.test_sync_timedb_day_raw_removal import (
    _make_closed_segment,
    _make_coordinator,
    _seal_day,
)

SUPERVISOR_SRC = Path(inspect.getsourcefile(st.run_sync_timedb_supervisor_loop))


def _spy_full_snapshot(monkeypatch):
  calls = {"n": 0}

  def boom(*_a, **_k):
    calls["n"] += 1
    raise AssertionError("build_archive_maintenance_snapshot must not run")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint.build_archive_maintenance_snapshot",
      boom,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.build_archive_maintenance_snapshot",
      boom,
      raising=False,
  )
  return calls


# --- R1 / R3 / R6 / R10 / R11 / R12 ---


def test_r1_unmanifested_closed_raw_excluded_without_full_snapshot(
    tmp_path, monkeypatch,
):
  """R1: verification_complete + unmanifested on-disk path stays in exclude."""
  day = datetime(2026, 6, 10)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  calls = _spy_full_snapshot(monkeypatch)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verified_count"] = 0
    _save_manifest(state._manifest_path, state._manifest)

  excluded = coord.rescan_exclude_paths()
  requeued = set(coord.paths_for_closed_raw_handoff_requeue(tar_path))
  assert str(seg) in excluded
  assert str(seg) in requeued
  assert calls["n"] == 0


def test_r3_deleted_listed_path_not_in_exclude(tmp_path, monkeypatch):
  """R3: isfile filter — missing path must not stay excluded forever."""
  day = datetime(2026, 6, 11)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  os.remove(str(seg))
  assert str(seg) not in coord.rescan_exclude_paths()


def test_r6_phase_done_manifest_fast_no_full_snapshot(tmp_path, monkeypatch):
  """R6: phase=done uses manifest-fast handoff; no full snapshot."""
  day = datetime(2026, 6, 12)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  calls = _spy_full_snapshot(monkeypatch)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  assert coord.should_handoff_to_ingest(tar_path)
  assert str(seg) in coord.rescan_exclude_paths()
  assert calls["n"] == 0


def test_r10_rescan_exclude_does_not_boolean_probe_handoff(tmp_path, monkeypatch):
  """R10: rescan_exclude_paths must not call handoff_paths_for_ingest as probe."""
  day = datetime(2026, 6, 13)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  probes = {"n": 0}
  real = state.handoff_paths_for_ingest

  def counted():
    probes["n"] += 1
    return real()

  monkeypatch.setattr(state, "handoff_paths_for_ingest", counted)
  # paths_for_closed_raw_handoff_requeue calls handoff once; exclude must not
  # add an extra boolean probe that doubles remaining-raw work.
  coord.rescan_exclude_paths()
  assert probes["n"] == 1


def test_r11_exclude_matches_requeue_for_active_handoff_day(tmp_path, monkeypatch):
  """R11: exclude ∩ day == requeue set (modulo quarantine)."""
  day = datetime(2026, 6, 14)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  excluded = set(coord.rescan_exclude_paths())
  requeued = set(coord.paths_for_closed_raw_handoff_requeue(tar_path))
  assert excluded == requeued


def test_r12_quarantine_path_not_in_exclude_or_requeue(tmp_path, monkeypatch):
  """R12: quarantine skip paths stay out of exclude and requeue."""
  day = datetime(2026, 6, 15)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  qpath = str(seg)
  coord = _make_coordinator(
      tmp_path,
      ingest_ready_fn=lambda _p: False,
      get_quarantine_skip_paths=lambda: {qpath},
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(qpath, zst, "skipped_quarantine", "quarantine")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  assert qpath not in coord.rescan_exclude_paths()
  assert qpath not in coord.paths_for_closed_raw_handoff_requeue(tar_path)


# --- R4 / R5 ---


def test_r4_mixed_verified_and_retryable_should_not_handoff_done(
    tmp_path, monkeypatch,
):
  """R4: verified pending delete blocks waiting-on-ingest PHASE_DONE."""
  day = datetime(2026, 6, 16)
  seg_v = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  host = seg_v.parent
  ts = int(datetime(day.year, day.month, day.day, 13, 0, 0).timestamp())
  seg_r = host / str(ts)
  seg_r.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg_r, (ts, ts))
  tar_path, zst = _seal_day(tmp_path, seg_v, day)
  # Keep both on disk; record mixed statuses.
  calls = _spy_full_snapshot(monkeypatch)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg_v), zst, "verified", "verified")
  state._record_entry(str(seg_r), zst, "skipped_not_in_archive", "not_in_sealed")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verified_count"] = 1
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  assert not coord.should_handoff_to_ingest(tar_path)
  assert state.phase() != PHASE_DONE
  assert calls["n"] == 0


def test_r5_tar_drop_false_when_closed_raw_on_disk_no_snapshot(
    tmp_path, monkeypatch,
):
  """R5: None snapshot + on-disk closed raw → tar_drop false."""
  day = datetime(2026, 6, 17)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  calls = _spy_full_snapshot(monkeypatch)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    _save_manifest(state._manifest_path, state._manifest)
  assert not coord.try_finish_tar_drop_if_ready(tar_path)
  assert os.path.isfile(tar_path)
  assert calls["n"] == 0


# --- R8 / R9 / R2 ---


def test_r8_two_days_both_excluded_zero_full_snapshots(tmp_path, monkeypatch):
  """R8: two tracked days; both handoff paths excluded; 0 full snapshots."""
  day_a = datetime(2026, 6, 18)
  day_b = datetime(2026, 6, 19)
  seg_a = _make_closed_segment(tmp_path, "cluster.integration.test", day_a)
  seg_b = _make_closed_segment(tmp_path, "cluster.integration.test", day_b)
  tar_a, zst_a = _seal_day(tmp_path, seg_a, day_a)
  tar_b, zst_b = _seal_day(tmp_path, seg_b, day_b)
  calls = _spy_full_snapshot(monkeypatch)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  for tar, zst, seg in ((tar_a, zst_a, seg_a), (tar_b, zst_b, seg_b)):
    state = coord._get_or_create_day(tar)
    state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed")
    with state._lock:
      state._manifest["phase"] = PHASE_DONE
      state._manifest["skipped_count"] = 1
      _save_manifest(state._manifest_path, state._manifest)
  excluded = coord.rescan_exclude_paths()
  assert str(seg_a) in excluded
  assert str(seg_b) in excluded
  assert calls["n"] == 0


def test_r2_day_scoped_listing_finds_path_missing_from_stale_snapshot(
    tmp_path, monkeypatch,
):
  """R2: stale empty accrual for a tar still finds closed raw via day-scoped."""
  day = datetime(2026, 6, 20)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  calls = _spy_full_snapshot(monkeypatch)
  # Stale snapshot claims no remaining raw for any day.
  snap = SimpleNamespace(remaining_raw_by_gz={})
  remaining = build_remaining_raw_for_daily_tar(
      str(tmp_path),
      "cluster.integration.test",
      str(tmp_path / "daily"),
      tar_path,
      maintenance_snapshot=snap,
      allow_full_snapshot=False,
  )
  flat = [p for paths in remaining.values() for p in paths]
  assert str(seg) in flat
  assert calls["n"] == 0


def test_r9_day_scoped_aligns_to_tar(tmp_path, monkeypatch):
  """R9: day-scoped helper only returns paths aligned to the daily tar."""
  day = datetime(2026, 6, 21)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  other_tar = str(tmp_path / "daily" / "2026-06-22.tar")
  open(other_tar, "wb").close()
  by_gz = build_day_scoped_closed_raw_by_gz(
      str(tmp_path),
      "cluster.integration.test",
      str(tmp_path / "daily"),
      other_tar,
  )
  flat = [p for paths in by_gz.values() for p in paths]
  assert str(seg) not in flat
  by_gz_ok = build_day_scoped_closed_raw_by_gz(
      str(tmp_path),
      "cluster.integration.test",
      str(tmp_path / "daily"),
      tar_path,
  )
  flat_ok = [p for paths in by_gz_ok.values() for p in paths]
  assert str(seg) in flat_ok


# --- R14 / R18 helpers ---


def test_r14_allow_full_false_skips_nested_snapshot(monkeypatch, tmp_path):
  """R14: allow_full_snapshot=False never builds full maintenance snapshot."""
  calls = _spy_full_snapshot(monkeypatch)
  out = build_remaining_raw_stats_by_daily_gz(
      str(tmp_path),
      "cluster.test",
      str(tmp_path / "daily"),
      maintenance_snapshot=None,
      allow_full_snapshot=False,
  )
  assert out == {}
  assert calls["n"] == 0


def test_r18_allow_full_true_still_may_build(monkeypatch, tmp_path):
  """R18: heavy path may still build when allow_full_snapshot=True."""
  calls = {"n": 0}

  def fake_build(*_a, **_k):
    calls["n"] += 1
    return SimpleNamespace(remaining_raw_by_gz={"z": ["/a"]})

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint.build_archive_maintenance_snapshot",
      fake_build,
  )
  out = build_remaining_raw_stats_by_daily_gz(
      str(tmp_path),
      "cluster.test",
      str(tmp_path / "daily"),
      maintenance_snapshot=None,
      allow_full_snapshot=True,
  )
  assert calls["n"] == 1
  assert out == {"z": ["/a"]}


# --- R7 ghost ---


def test_r7_ghost_deleted_kick_without_full_snapshot(tmp_path, monkeypatch):
  """R7: ghost deleted=True on disk kicks without full snapshot."""
  day = datetime(2026, 6, 23)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  calls = _spy_full_snapshot(monkeypatch)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["entries"][str(seg)]["deleted"] = True
    state._manifest["phase"] = PHASE_DONE
    state._manifest["deleted_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  assert state.needs_ghost_delete_retry()
  action = coord.kick_closed_raw_unblock(tar_path, reason="r7")
  assert action in ("ghost_delete", "delete_reopen", "verify", "noop", "quarantine_terminal")
  assert calls["n"] == 0


# --- Supervisor / architecture source contracts R13 / R15 / R23–R28 ---


def test_r13_boundary_exclusions_once_reuse_contract():
  """R13: boundary computes exclusions once; idle merges reuse local var."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  # Async boundary must not call sync rescan_pending_stats_files inline.
  boundary = src.split("if chunk_counter % rescan_every_chunks == 0:", 1)[1]
  boundary = boundary.split("_finalize_archive_slots_if_needed(", 2)[0]
  assert "rescan_pending_stats_files(" not in boundary
  assert "_start_pending_rescan_async" in src
  assert "_drain_pending_rescan_future" in src
  # Idle refill: processed_exclude = _rescan... then reuse (not kwargs inline).
  assert src.count("processed_exclude=_rescan_processed_exclusions()") == 0
  assert "processed_exclude = _rescan_processed_exclusions()" in src


def test_r15_day_close_enqueue_eligible_no_full_remaining_build():
  """R15: _day_close_enqueue_eligible must not call build_remaining_raw_stats_by_daily_gz."""
  src = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  fn = src.split("def _day_close_enqueue_eligible", 1)[1].split(
      "\n  day_close_manifest.submit_eligible_fn", 1,
  )[0]
  assert "build_remaining_raw_stats_by_daily_gz" not in fn
  assert "remaining = {}" in fn or 'remaining = {}' in fn


def test_r23_supplement_uses_full_rescan_exclude():
  """R23: async supplement path passes _rescan_processed_exclusions()."""
  src = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  fn = src.split("def _supplement_pending_while_rescan_inflight", 1)[1].split(
      "\n  def ", 1,
  )[0]
  assert "processed_exclude = _rescan_processed_exclusions()" in fn
  assert "supplement_pending_paths_from_closed_paths" in fn


def test_r24_r27_async_inflight_defers_archive_and_idle():
  """R24/R27: archive defer when scan in flight; idle waits on async."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "allow_defer=bool(pending_stats_files) or scan_inflight" in src
  assert "pending empty while async rescan in flight" in src
  assert "_pending_rescan_in_flight()" in src


def test_r25_single_flight_async_owner():
  """R25: SessionSingleFlightExecutor pending-rescan; no dual sync+async discover."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert 'thread_role="pending-rescan"' in src
  assert "SessionSingleFlightExecutor" in src
  assert src.count("_start_pending_rescan_async") >= 1


def test_r28_maintenance_snapshot_wired_before_first_exclude():
  """R28: day_raw get_maintenance_snapshot prefers accrual then coordinator."""
  src = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  assert "day_raw_removal.get_maintenance_snapshot = _get_maintenance_snapshot_for_day_raw" in src
  fn = src.split("def _get_maintenance_snapshot_for_day_raw", 1)[1].split(
      "\n  day_raw_removal.get_maintenance_snapshot", 1,
  )[0]
  assert "startup_archive_scan.get_snapshot()" in fn
  assert "_get_accrual_remaining_raw_by_gz" in fn


def test_r19_accrual_remaining_copied_under_lock():
  """R19: accrual remaining map is copied under lock for consumers."""
  src = inspect.getsource(st.run_sync_timedb_supervisor_loop)
  fn = src.split("def _get_accrual_remaining_raw_by_gz", 1)[1].split("\n  def ", 1)[0]
  assert "with archive_janitor._accrual_snapshot_lock:" in fn
  assert "return dict(snap.remaining_raw_by_gz)" in fn


# --- Janitor R16 / R17 / R22 ---


def test_r16_janitor_accrual_only_helper_forbids_full_snapshot():
  """R16: ArchiveJanitor light/discover remaining uses allow_full_snapshot=False."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_janitor as jan

  src = Path(inspect.getsourcefile(jan.ArchiveJanitor)).read_text(encoding="utf-8")
  assert "def _remaining_raw_from_accrual_only" in src
  assert "allow_full_snapshot=False" in src
  # Light pass must call accrual-only helper, not bare build with default True.
  assert "_remaining_raw_from_accrual_only()" in src


def test_r22_day_raw_build_remaining_allow_full_false():
  """R22: DayRawRemovalCoordinator never full-snapshots remaining-raw."""
  from hpcperfstats.dbload.lib import sync_timedb_day_raw_removal as drr

  src = inspect.getsource(drr._DayRawRemovalState._build_remaining_raw_for_daily_tar)
  assert "allow_full_snapshot=False" in src
