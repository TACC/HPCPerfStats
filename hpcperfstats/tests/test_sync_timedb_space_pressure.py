"""Archive free-space pressure: appends and restores yield to day close.

With ``sync_archive_space_pressure_free_bytes`` = X set, pressure starts when
the archive filesystem has less than X free and lasts until it has 2X free.
Under pressure nothing grows the disk but a seal and a reflink clone append
that cannot grow the filesystem (open tar or new tar): appends that would
restore a sealed day or copy raw bytes stay parked, every restore (append,
day close, ingest) is refused, and dedupe, union rebuild, salvage and repair
wait. The one-sealed-day restore slot no longer binds: restores run only
without pressure, where the slot never applied.
"""

from __future__ import annotations

import inspect
import io
import os
import subprocess
import tarfile

import pytest

from hpcperfstats.dbload.lib import (
  conf_parser as cfg,
  sync_timedb_archive_helpers as ah,
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
  sync_timedb_space_pressure as sp,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore

GIB = 1 << 30
TIB = 1 << 40


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
  sp.reset_for_tests()
  monkeypatch.setattr(cfg, "get_sync_archive_min_free_bytes", lambda: GIB)
  # tmp_path is on a reflink filesystem; these tests measure raw-byte
  # (copying) appends. test_append_batch_need_clone_capable covers cloning.
  monkeypatch.setattr(qo.tca, "clone_capable", lambda *_a, **_k: False)
  yield
  sp.reset_for_tests()


@pytest.mark.parametrize(
  ("capable", "expected"),
  [(True, 7 + 3 * qo.tca.CLONE_HEADER_BYTES), (False, 7 + 3 * 1000)],
)
def test_append_batch_need_clone_capable(
  monkeypatch, tmp_path, capable, expected
):
  """A cloned append writes headers, not data: reserve per-member bytes."""
  qo.reset_append_day_lists_for_tests()
  for i in range(3):
    raw = tmp_path / f"raw{i}"
    raw.write_bytes(b"r" * 1000)
    qo._APPEND_DAY_LISTS.add("2026-09-01", _claim(str(raw)))
  seen = []

  def _capable(tar, sample):
    seen.append((tar, sample))
    return capable

  monkeypatch.setattr(qo.tca, "clone_capable", _capable)
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: 7)
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  tar = str(tmp_path / "2026-09-01.tar")
  assert qo._append_batch_need("2026-09-01", tar, 3) == expected
  assert seen == [(tar, str(tmp_path / "raw0"))]
  qo.reset_append_day_lists_for_tests()


def test_append_batch_need_unknown_day_reserves_raw_bytes(
  monkeypatch, tmp_path
):
  qo.reset_append_day_lists_for_tests()
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: 0)
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  calls = []
  monkeypatch.setattr(
    qo.tca, "clone_capable", lambda *a: calls.append(a) or True
  )
  assert qo._append_batch_need("2026-09-02", str(tmp_path / "x.tar"), 2) == 0
  assert calls == []


def _set(monkeypatch, *, threshold: int, free: int):
  monkeypatch.setattr(
    cfg, "get_sync_archive_space_pressure_free_bytes", lambda: threshold
  )
  monkeypatch.setattr(sp, "_free_bytes", lambda _p: free)


@pytest.mark.parametrize(
  ("raw", "expected"),
  [
    ("0", 0),
    ("", 0),
    ("10737418240", 10737418240),
    ("4T", 4 << 40),
    ("500G", 500 << 30),
    ("1.5t", int(1.5 * (1 << 40))),
    ("64m", 64 << 20),
  ],
)
def test_byte_size_setting_accepts_suffixes(raw, expected):
  assert cfg.parse_byte_size(raw) == expected


def test_default_pressure_threshold_is_one_tib():
  raw = cfg.INI_OPTION_DEFAULTS["sync_archive_space_pressure_free_bytes"]
  assert cfg.parse_byte_size(raw) == 1 << 40


def test_gate_off_never_pressure(monkeypatch):
  _set(monkeypatch, threshold=0, free=0)
  assert not sp.under_pressure("/d")
  assert sp.select_append_day(["2026-09-01"], set()) is sp.ALL_DAYS
  assert sp.census_token("/d") == ""


def test_pressure_starts_below_x_and_ends_at_2x(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB + 1)
  assert not sp.under_pressure("/d")
  _set(monkeypatch, threshold=TIB, free=TIB - 1)
  assert sp.under_pressure("/d")
  _set(monkeypatch, threshold=TIB, free=int(1.9 * TIB))
  assert sp.under_pressure("/d")
  _set(monkeypatch, threshold=TIB, free=2 * TIB)
  assert not sp.under_pressure("/d")


def _statfs(monkeypatch, *, free=None, error=None):
  def disk_usage(_path):
    if error:
      raise error
    return type("U", (), {"free": free})()

  monkeypatch.setattr(sp.shutil, "disk_usage", disk_usage)


def test_statfs_error_is_unknown_not_a_full_disk(monkeypatch, tmp_path):
  """F6: EIO on statfs used to read as 0 free and turn pressure on."""
  monkeypatch.setattr(
    cfg, "get_sync_archive_space_pressure_free_bytes", lambda: TIB
  )
  _statfs(monkeypatch, error=OSError(5, "EIO"))
  assert not sp.under_pressure(str(tmp_path))
  _statfs(monkeypatch, free=TIB // 2)
  assert sp.under_pressure(str(tmp_path))
  _statfs(monkeypatch, error=OSError(5, "EIO"))
  assert sp.under_pressure(str(tmp_path))  # unknown keeps the state
  assert not sp.fits_hard_reserve(str(tmp_path), 1)
  assert "free=unknown" in sp.census_token(str(tmp_path))


def test_missing_archive_dir_is_not_measured_on_a_parent(monkeypatch, tmp_path):
  """F7: a lost mount must not be measured on the filesystem beneath it."""
  missing = tmp_path / "mnt" / "daily"
  assert sp._free_bytes(str(missing / "2026-09-07.tar")) is None
  assert sp._free_bytes(str(missing)) is None
  missing.mkdir(parents=True)
  assert sp._free_bytes(str(missing / "2026-09-07.tar")) > 0


def test_no_appends_while_a_day_close_finishes_a_day(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  sp.note_day_close_stage("2026-09-07", "seal")
  assert sp.select_append_day(["2026-09-01"], set()) is None


def test_day_close_checks_alone_do_not_block_appends(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  sp.note_day_close_stage("2026-09-07", "disk_remaining_raw")
  sp.note_day_close_stage("2026-09-08", "wait_on_ingest")
  assert sp.select_append_day(["2026-09-03", "2026-09-01"], set()) == (
    "2026-09-01"
  )


def test_one_day_at_a_time_then_hold_for_its_day_close(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  t = [1000.0]
  monkeypatch.setattr(sp.time, "monotonic", lambda: t[0])
  assert sp.select_append_day(["2026-09-01", "2026-09-02"], set()) == (
    "2026-09-01"
  )
  # The chosen day keeps the slot while it has parked or in-flight appends,
  # even when an older day shows up.
  assert sp.select_append_day(["2026-08-30", "2026-09-01"], set()) == (
    "2026-09-01"
  )
  assert sp.select_append_day(["2026-09-02"], {"2026-09-01"}) == "2026-09-01"
  # Its appends are done: hold so its day close can start.
  assert sp.select_append_day(["2026-09-02"], set()) is None
  # Its day close gives space back; once that ends, the next day starts.
  sp.note_day_close_stage("2026-09-01", "seal")
  sp.note_space_freed()
  assert sp.select_append_day(["2026-09-02"], set()) is None
  sp.clear_day_close("2026-09-01")
  assert sp.select_append_day(["2026-09-02"], set()) == "2026-09-02"


def test_a_day_close_that_frees_nothing_does_not_end_the_hold(monkeypatch):
  """F1: reconcile then yield used to release the hold on stage entry."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  t = [1000.0]
  monkeypatch.setattr(sp.time, "monotonic", lambda: t[0])
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"
  assert sp.select_append_day(["2026-09-02"], set()) is None
  for stage in ("reconcile_merge", "dedupe", "seal"):
    sp.note_day_close_stage("2026-09-01", stage)
  sp.clear_day_close("2026-09-01")
  assert sp.select_append_day(["2026-09-02"], set()) is None
  t[0] += sp.DAY_CLOSE_START_GRACE_S + 1
  assert sp.select_append_day(["2026-09-02"], set()) == "2026-09-02"


def test_space_freed_before_the_hold_does_not_end_it(monkeypatch):
  """A free from an earlier day close does not count for this hold."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  sp.note_space_freed()
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"
  assert sp.select_append_day(["2026-09-02"], set()) is None


def test_drain_clears_only_its_own_job_for_a_shared_day(monkeypatch):
  """F3: the tar-path job ending must not clear the bare-day job's seal."""
  from concurrent.futures import Future

  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  done = Future()
  done.set_result("yielded")
  sp.note_day_close_stage("2026-09-07", "seal")
  qo._drain_day_close_ready(
    SyncTimedbJobStore(""),
    inflight={"/d/2026-09-07.tar": done},
    leases={},
  )
  assert sp.select_append_day(["2026-09-01"], set()) is None
  qo._drain_day_close_ready(
    SyncTimedbJobStore(""), inflight={"2026-09-07": done}, leases={}
  )
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"


def test_seal_yield_checks_do_not_hold_appends(monkeypatch):
  """F2: only the seal itself counts, not its unarchived/discover checks."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  sp.note_day_close_stage("2026-09-07", "seal_check")
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"


def _seal_claim_stages(tmp_path, monkeypatch, *, found):
  from hpcperfstats.tests.test_sync_timedb_seal_after_full_discover import (
    _seal_harness,
  )

  claim, calls, _logs = _seal_harness(tmp_path, monkeypatch)
  monkeypatch.setattr(qo, "_closed_raw_not_archived", lambda *a: found)
  monkeypatch.setattr(
    qo, "_handoff_retryable_paths_to_ingest", lambda *a, **k: 0
  )
  seen = []
  monkeypatch.setattr(
    sp, "note_day_close_stage", lambda _d, stage: seen.append(stage)
  )
  return claim, calls, seen


def test_day_close_yielding_at_seal_never_reaches_a_finishing_stage(
  tmp_path, monkeypatch
):
  claim, calls, seen = _seal_claim_stages(
    tmp_path, monkeypatch, found=["/late"]
  )
  assert claim() == "yielded"
  assert calls == []
  assert seen[-1] == "seal_check"
  assert "seal" not in seen[seen.index("seal_check") :]


def test_day_close_that_seals_marks_the_seal_finishing(tmp_path, monkeypatch):
  claim, calls, seen = _seal_claim_stages(tmp_path, monkeypatch, found=[])
  claim()
  assert calls == [False]
  assert "seal" in seen[seen.index("seal_check") :]


def test_day_close_publishes_stages_under_its_job_identity(
  tmp_path, monkeypatch
):
  from hpcperfstats.tests.test_sync_timedb_seal_after_full_discover import (
    _seal_harness,
  )

  claim, _calls, _logs = _seal_harness(tmp_path, monkeypatch)
  monkeypatch.setattr(qo, "_closed_raw_not_archived", lambda *a: [])
  keys = set()
  monkeypatch.setattr(
    sp, "note_day_close_stage", lambda job, _stage: keys.add(job)
  )
  claim()  # identity "2020-01-10"
  tar = str(tmp_path / "daily" / "2020-01-10.tar")
  qo._run_day_close_job(
    tar,
    tgz_archive_dir=str(tmp_path / "daily"),
    archive_data_dir=str(tmp_path / "archive"),
    log_fn=lambda *_a, **_k: None,
  )
  assert keys == {"2020-01-10", tar}


def test_raw_delete_notes_space_freed(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_day_raw_removal as drr

  claim, _calls, _seen = _seal_claim_stages(tmp_path, monkeypatch, found=[])
  monkeypatch.setattr(
    drr.DayRawRemovalCoordinator, "apply_batch_delete", lambda _s, _t: 3
  )
  freed = []
  monkeypatch.setattr(sp, "note_space_freed", lambda: freed.append(1))
  claim()
  assert freed


def test_hold_expires_when_no_day_close_ever_starts(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  t = [1000.0]
  monkeypatch.setattr(sp.time, "monotonic", lambda: t[0])
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"
  assert sp.select_append_day(["2026-09-02"], set()) is None
  t[0] += sp.DAY_CLOSE_START_GRACE_S + 1
  assert sp.select_append_day(["2026-09-02"], set()) == "2026-09-02"


def test_nothing_parked_keeps_claiming_instead_of_blocking(monkeypatch):
  """None would stop the fill from claiming, so no day could ever park."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert sp.select_append_day([], set()) == ""
  assert sp.select_append_day(["2026-09-04"], set()) == "2026-09-04"


def test_fill_under_pressure_claims_then_submits_every_clone_day(
  monkeypatch, tmp_path
):
  """End to end through _fill_append_slots: copying days wait, clones go."""
  from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
    note_day_remainder,
  )

  qo.reset_append_day_lists_for_tests()
  client = SyncTimedbJobStore("")
  for day in ("2026-09-02", "2026-09-01"):
    raw = tmp_path / f"raw-{day}"
    raw.write_bytes(b"r")
    note_day_remainder("append", day, 1)
    jq.enqueue_list_job(client, kind=jq.JOB_KIND_APPEND, identity=str(raw))
  monkeypatch.setattr(
    qo,
    "daily_tar_path_for_stats_path",
    lambda p, d: f"{d}/{p.rsplit('raw-', 1)[1]}.tar",
  )
  monkeypatch.setattr(qo, "_discover_bg_is_busy", lambda: False)
  monkeypatch.setattr(qo, "cgroup_admit_headroom_ok", lambda _m: True)
  monkeypatch.setattr(qo, "cgroup_admit_file_cache_ok", lambda _m: True)
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: 0)
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  submitted = []

  class _Pool:
    def apply_async(self, fn, args):
      submitted.append(args[0][0].rsplit("/", 1)[1])
      return object()

  kwargs = {
    "cap": 4,
    "claims": {},
    "archive_pool": _Pool(),
    "tgz_archive_dir": str(tmp_path),
  }
  qo._fill_append_slots(client, inflight={}, **kwargs)
  qo._fill_append_slots(client, inflight={}, **kwargs)
  assert submitted == []  # copying site: nothing may grow the disk
  monkeypatch.setattr(qo.tca, "clone_capable", lambda *_a, **_k: True)
  qo._fill_append_slots(client, inflight={}, **kwargs)
  assert sorted(submitted) == ["2026-09-01.tar", "2026-09-02.tar"]


def test_pressure_end_resumes_all_days(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"
  _set(monkeypatch, threshold=TIB, free=2 * TIB)
  assert sp.select_append_day(["2026-09-02"], set()) is sp.ALL_DAYS


def test_pressure_ending_mid_selection_leaves_no_append_day(monkeypatch):
  """F5: a census under_pressure() that ends pressure while ``fits`` runs."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)

  def fits_then_pressure_ends(_day):
    _set(monkeypatch, threshold=TIB, free=2 * TIB)
    sp.under_pressure("/d")
    return True

  got = sp.select_append_day(["2026-09-01"], set(), fits_then_pressure_ends)
  assert got is sp.ALL_DAYS
  assert sp._STATE["append_day"] is None
  assert sp._STATE["hold_since"] is None


def test_pressure_ending_before_the_hold_write_sets_no_hold(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"

  def fits_then_pressure_ends(_day):
    _set(monkeypatch, threshold=TIB, free=2 * TIB)
    sp.under_pressure("/d")
    return False

  # 09-01 still parked but no longer fits: the slot check calls ``fits``.
  got = sp.select_append_day(["2026-09-01"], set(), fits_then_pressure_ends)
  assert got is sp.ALL_DAYS
  assert sp._STATE["hold_since"] is None


def test_hard_reserve_check_leaves_room_for_the_reseal(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=100 * GIB)
  # 100G - (40G restore + 10G raw) - 20G (reseal of the restore) = 30G.
  assert sp.fits_hard_reserve("/d/x.tar", 50 * GIB, restore_need=40 * GIB)
  assert not sp.fits_hard_reserve("/d/x.tar", 90 * GIB, restore_need=60 * GIB)


def test_census_token(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=int(3.2 * TIB))
  assert sp.census_token("/d") == " space=ok free=3.2T pressure_below=1.0T"
  _set(monkeypatch, threshold=4 * TIB, free=int(3.2 * TIB))
  sp.select_append_day(["2026-09-13"], set())
  assert sp.census_token("/d") == (
    " space=pressure free=3.2T resume_at=8.0T append_day=2026-09-13"
    " restore_slot=none"
  )


# --- reflink era: only sealed-day restores and copies are gated ----------


def _parked_days(monkeypatch, tmp_path, *, days, finishing=False):
  """Park one raw file per day; ``days`` maps day -> 'open'|'sealed'|'new'."""
  qo.reset_append_day_lists_for_tests()
  daily = tmp_path / "daily"
  daily.mkdir(exist_ok=True)
  for day, kind in days.items():
    raw = tmp_path / f"raw-{day}"
    raw.write_bytes(b"r" * 100)
    qo._APPEND_DAY_LISTS.add(day, _claim(str(raw)))
    tar = daily / f"{day}.tar"
    if kind == "open":
      tar.write_bytes(b"t" * 10240)
    if kind in ("open", "sealed"):
      (daily / f"{day}.tar.zst").write_bytes(b"z")
  monkeypatch.setattr(
    qo,
    "daily_tar_path_for_stats_path",
    lambda p, d: f"{d}/{p.rsplit('raw-', 1)[1]}.tar",
  )
  monkeypatch.setattr(
    sp,
    "restore_bytes_estimate",
    lambda t: 0 if os.path.isfile(t) or not os.path.isfile(t + ".zst") else GIB,
  )
  if finishing:
    sp.note_day_close_stage("/d/2026-08-01.tar", "seal")
  return str(daily)


def test_clone_days_append_while_a_day_close_is_finishing(
  monkeypatch, tmp_path
):
  """Under pressure a clone append to an open tar never waits (cannot grow)."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  monkeypatch.setattr(qo.tca, "clone_capable", lambda *_a, **_k: True)
  daily = _parked_days(
    monkeypatch,
    tmp_path,
    days={"2026-09-01": "open", "2026-09-02": "new", "2026-09-03": "sealed"},
    finishing=True,
  )
  got = qo._select_append_day({}, daily, 256)
  assert got == frozenset({"2026-09-01", "2026-09-02"})  # sealed day waits
  qo.reset_append_day_lists_for_tests()


def test_no_pressure_allows_every_day(monkeypatch, tmp_path):
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  daily = _parked_days(monkeypatch, tmp_path, days={"2026-09-03": "sealed"})
  assert qo._select_append_day({}, daily, 256) is sp.ALL_DAYS
  qo.reset_append_day_lists_for_tests()


def test_one_sealed_day_expanded_at_a_time(monkeypatch, tmp_path):
  """The restore slot: a second sealed day waits while the first's tar lives."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  first = tmp_path / "2026-09-03.tar"
  second = str(tmp_path / "2026-09-04.tar")
  sp.note_expansion(str(first))
  sp.reserve(str(first), GIB, tar_path=str(first))  # restore in flight
  assert not sp.restore_slot_free(second)
  assert sp.restore_slot_free(str(first))  # its own further appends
  sp.release(str(first))
  first.write_bytes(b"t")  # restored tar on disk: still holds the slot
  assert not sp.restore_slot_free(second)
  assert "restore_slot=2026-09-03" in sp.census_token(str(tmp_path))
  first.unlink()  # sealed away / dropped
  assert sp.restore_slot_free(second)
  assert "restore_slot=none" in sp.census_token(str(tmp_path))


def test_failed_restore_frees_the_slot(monkeypatch, tmp_path):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  sp.note_expansion(str(tmp_path / "2026-09-03.tar"))  # no tar, no reservation
  assert sp.restore_slot_free(str(tmp_path / "2026-09-04.tar"))


def test_slot_is_ignored_without_pressure(monkeypatch, tmp_path):
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  held = tmp_path / "2026-09-03.tar"
  held.write_bytes(b"t")
  sp.note_expansion(str(held))
  assert sp.restore_slot_free(str(tmp_path / "2026-09-04.tar"))


def test_append_restore_waits_for_pressure_to_end(monkeypatch, tmp_path):
  _set(monkeypatch, threshold=5 * TIB, free=4 * TIB)
  held = tmp_path / "2026-09-03.tar"
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: GIB)
  qo.reset_append_day_lists_for_tests()
  raw = tmp_path / "raw"
  raw.write_bytes(b"r")
  qo._APPEND_DAY_LISTS.add("2026-09-04", _claim(str(raw)))
  tar = str(tmp_path / "2026-09-04.tar")
  assert qo._append_batch_need("2026-09-04", tar, 1) is None
  assert not held.exists()
  _set(monkeypatch, threshold=5 * TIB, free=11 * TIB)
  assert qo._append_batch_need("2026-09-04", tar, 1) == GIB + 1
  qo.reset_append_day_lists_for_tests()


def _tar_bytes(n: int) -> bytes:
  buf = io.BytesIO()
  with tarfile.open(fileobj=buf, mode="w") as tf:
    info = tarfile.TarInfo("h/1")
    info.size = n
    tf.addfile(info, io.BytesIO(b"a" * n))
  return buf.getvalue()


def test_restore_estimate_reads_zst_frame_content_size(tmp_path):
  raw = _tar_bytes(300000)
  src = tmp_path / "src.tar"
  src.write_bytes(raw)
  zst = tmp_path / "2026-09-07.tar.zst"
  subprocess.run(["zstd", "-q", "-o", str(zst), str(src)], check=True)
  tar = str(tmp_path / "2026-09-07.tar")
  assert sp.restore_bytes_estimate(tar) == len(raw)


def test_restore_estimate_without_content_size_is_conservative(tmp_path):
  raw = _tar_bytes(300000)
  zst = tmp_path / "2026-09-07.tar.zst"
  subprocess.run(["zstd", "-q", "-o", str(zst)], input=raw, check=True)
  est = sp.restore_bytes_estimate(str(tmp_path / "2026-09-07.tar"))
  assert est == zst.stat().st_size * sp.UNKNOWN_RATIO


def test_restore_estimate_zero_when_tar_open_or_nothing_sealed(tmp_path):
  tar = tmp_path / "2026-09-07.tar"
  assert sp.restore_bytes_estimate(str(tar)) == 0
  tar.write_bytes(_tar_bytes(10))
  (tmp_path / "2026-09-07.tar.zst").write_bytes(b"x" * 100)
  assert sp.restore_bytes_estimate(str(tar)) == 0


def _sealed_day(tmp_path):
  tar = tmp_path / "2026-09-07.tar"
  zst = tmp_path / "2026-09-07.tar.zst"
  subprocess.run(
    ["zstd", "-q", "-o", str(zst)], input=_tar_bytes(1000), check=True
  )
  return tar, zst


def test_growth_restore_denied_under_pressure(monkeypatch, tmp_path):
  tar, zst = _sealed_day(tmp_path)
  before = zst.read_bytes()
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert (
    ah.ensure_daily_tar_restored_for_append(str(tar), 1, space_gate="growth")
    is sp.SPACE_DENIED
  )
  assert not tar.exists()
  assert zst.read_bytes() == before


def test_day_close_restore_denied_under_pressure(monkeypatch, tmp_path):
  tar, zst = _sealed_day(tmp_path)
  before = zst.read_bytes()
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert (
    ah.ensure_daily_tar_restored_for_append(str(tar), 1, space_gate="finish")
    is sp.SPACE_DENIED
  )
  assert not tar.exists()
  assert zst.read_bytes() == before
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  assert ah.ensure_daily_tar_restored_for_append(
    str(tar), 1, space_gate="finish"
  )
  assert tar.stat().st_size > 0


def test_finish_restore_still_keeps_hard_reserve(monkeypatch, tmp_path):
  tar, _zst = _sealed_day(tmp_path)
  _set(monkeypatch, threshold=TIB, free=GIB)
  assert (
    ah.ensure_daily_tar_restored_for_append(str(tar), 1, space_gate="finish")
    is sp.SPACE_DENIED
  )
  assert not tar.exists()


def test_space_gate_is_required():
  """F13: a new caller must choose a mode, not inherit the strictest."""
  param = inspect.signature(ah.ensure_daily_tar_restored_for_append).parameters[
    "space_gate"
  ]
  assert param.default is inspect.Parameter.empty


def test_space_denied_open_tar_restore_is_a_transient_defer(
  monkeypatch, tmp_path
):
  """F13: a denied repair restore is env:ENOSPC, not repair_failed."""
  tar, _zst = _sealed_day(tmp_path)
  _set(monkeypatch, threshold=TIB, free=GIB)
  got = ah.ensure_open_daily_tar_readable(str(tar), caller="test")
  assert (got.readable, got.transient, got.transient_reason) == (
    False,
    True,
    "env:ENOSPC",
  )


def test_space_denied_restore_never_sets_day_skip(monkeypatch, tmp_path):
  """F13: the ingest lookup used to set a sticky open_tar_gnu_tvf_failed."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_members_coord as mc

  tar, zst = _sealed_day(tmp_path)
  _set(monkeypatch, threshold=TIB, free=GIB)
  skipped = []
  monkeypatch.setattr(
    mc, "set_archive_day_ingest_skip", lambda *a, **k: skipped.append(a)
  )
  with pytest.raises(mc.ArchiveMembersStoreUnavailableError) as exc:
    ah._ensure_open_tar_readable_for_ingest_lookup(
      str(tar), caller="ingest_membership", compressed_canonical=str(zst)
    )
  assert not isinstance(exc.value, mc.ArchiveDayIngestSkipError)
  assert "ENOSPC" in str(exc.value)
  assert skipped == []


def test_append_path_restore_is_not_regated(monkeypatch, tmp_path):
  """The append path is gated at dispatch, so its restore is not re-gated."""
  tar, _zst = _sealed_day(tmp_path)
  _set(monkeypatch, threshold=TIB, free=0)
  assert ah.ensure_daily_tar_restored_for_append(str(tar), 1, space_gate="off")
  assert tar.stat().st_size > 0


def _claim(identity: str):
  return jq.ClaimedJob(
    kind=jq.JOB_KIND_APPEND,
    identity=identity,
    owner_token="n:h:b:1",
    deadline=1.0e9,
    score=0.0,
  )


def _submit(
  monkeypatch, tmp_path, days, *, free: int, restore=0, restore_for=None
):
  from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
    note_day_remainder,
  )

  qo.reset_append_day_lists_for_tests()
  for day in days:
    raw = tmp_path / f"raw-{day}"
    raw.write_bytes(b"r" * 1000)
    note_day_remainder("append", day, 1)
    qo._APPEND_DAY_LISTS.add(day, _claim(str(raw)))
  monkeypatch.setattr(
    qo,
    "daily_tar_path_for_stats_path",
    lambda p, d: f"{d}/{p.rsplit('raw-', 1)[1]}.tar",
  )
  monkeypatch.setattr(qo, "_discover_bg_is_busy", lambda: False)
  monkeypatch.setattr(
    sp,
    "restore_bytes_estimate",
    lambda t: (restore_for or {}).get(t.rsplit("/", 1)[1][:10], restore),
  )
  _set(monkeypatch, threshold=TIB, free=free)
  submitted = []

  class _Pool:
    def apply_async(self, fn, args):
      submitted.append(args[0][0])
      return object()

  qo._try_submit_pending_append_days(
    client=SyncTimedbJobStore(""),
    cap=4,
    inflight={},
    claims={},
    archive_pool=_Pool(),
    tgz_archive_dir=str(tmp_path),
    batch_size=4,
    allow_partial=False,
  )
  return [p.rsplit("/", 1)[1] for p in submitted]


def test_submit_without_pressure_feeds_every_day(monkeypatch, tmp_path):
  got = _submit(monkeypatch, tmp_path, ["2026-09-01", "2026-09-02"], free=TIB)
  assert got == ["2026-09-01.tar", "2026-09-02.tar"]


def test_submit_under_pressure_feeds_no_copying_day(monkeypatch, tmp_path):
  got = _submit(
    monkeypatch, tmp_path, ["2026-09-02", "2026-09-01"], free=TIB // 2
  )
  assert got == []
  assert qo._APPEND_DAY_LISTS.peek_len("2026-09-01") == 1
  assert qo._APPEND_DAY_LISTS.peek_len("2026-09-02") == 1


def test_submit_under_pressure_waits_for_finishing_day_close(
  monkeypatch, tmp_path
):
  sp.note_day_close_stage("2026-08-30", "post_seal_verify")
  got = _submit(monkeypatch, tmp_path, ["2026-09-01"], free=TIB // 2)
  assert got == []
  assert qo._APPEND_DAY_LISTS.peek_len("2026-09-01") == 1


def test_submit_skips_day_whose_restore_breaks_hard_reserve(
  monkeypatch, tmp_path
):
  got = _submit(
    monkeypatch, tmp_path, ["2026-09-01"], free=100 * GIB, restore=90 * GIB
  )
  assert got == []
  assert qo._APPEND_DAY_LISTS.peek_len("2026-09-01") == 1


def test_select_skips_days_that_do_not_fit(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  fits = {"2026-09-01": False, "2026-09-02": True}.get
  assert sp.select_append_day(["2026-09-02", "2026-09-01"], set(), fits) == (
    "2026-09-02"
  )
  sp.reset_for_tests()
  assert sp.select_append_day(["2026-09-01"], set(), lambda _d: False) == ""


def test_pre_seal_verify_alone_does_not_hold_appends(monkeypatch):
  """Review #2: verify batches yield every claim; they are not finishing."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  sp.note_day_close_stage("2026-09-07", "pre_seal_verify")
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"


@pytest.mark.parametrize(
  ("raw", "expected"),
  [("2T", 2 << 40), ("0", 0), ("1 TiB please", 1 << 40), ("", 0)],
)
def test_bad_ini_value_falls_back_to_default(raw, expected):
  """Review #3: a typo must not raise on the append/census/seal threads."""
  assert cfg.space_pressure_bytes_from_ini(raw) == expected


def test_reservations_count_against_the_hard_reserve(monkeypatch):
  """Review #4: concurrent admissions see each other's bytes."""
  _set(monkeypatch, threshold=TIB, free=100 * GIB)
  assert sp.fits_hard_reserve("/d/x.tar", 60 * GIB)
  sp.reserve("/d/y.tar", 50 * GIB)
  assert not sp.fits_hard_reserve("/d/x.tar", 60 * GIB)
  sp.release("/d/y.tar")
  assert sp.reserved_bytes() == 0


def test_submitted_batch_holds_a_reservation(monkeypatch, tmp_path):
  got = _submit(
    monkeypatch, tmp_path, ["2026-09-01"], free=3 * TIB, restore=5 * GIB
  )
  assert got == ["2026-09-01.tar"]
  assert sp.reserved_bytes() == 5 * GIB + 1000


def test_reservation_shrinks_as_its_tar_grows(tmp_path):
  """F9: free already reflects written bytes; do not count them twice."""
  tar = tmp_path / "2026-09-07.tar"
  sp.reserve("k", 1000, tar_path=str(tar))
  assert sp.reserved_bytes() == 1000
  tmp = tmp_path / "2026-09-07.tar.decomp.tmp"
  tmp.write_bytes(b"x" * 300)
  assert sp.reserved_bytes() == 700
  tmp.replace(tar)  # the restore's os.replace keeps the count
  assert sp.reserved_bytes() == 700
  with tar.open("ab") as fh:
    fh.write(b"y" * 900)
  assert sp.reserved_bytes() == 0
  sp.reserve("plain", 50)
  assert sp.reserved_bytes() == 50


def test_reservation_ignores_a_tar_that_shrank(tmp_path):
  tar = tmp_path / "2026-09-07.tar"
  tar.write_bytes(b"x" * 500)
  sp.reserve("k", 100, tar_path=str(tar))
  tar.write_bytes(b"x" * 10)  # e.g. a rollback; never more than reserved
  assert sp.reserved_bytes() == 100


def _one_parked_day(monkeypatch, tmp_path, *, restore):
  qo.reset_append_day_lists_for_tests()
  raw = tmp_path / "raw-2026-09-01"
  raw.write_bytes(b"r")
  qo._APPEND_DAY_LISTS.add("2026-09-01", _claim(str(raw)))
  monkeypatch.setattr(
    qo,
    "daily_tar_path_for_stats_path",
    lambda p, d: f"{d}/{p.rsplit('raw-', 1)[1]}.tar",
  )
  monkeypatch.setattr(qo, "_discover_bg_is_busy", lambda: False)
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: restore)
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  return lambda pool: qo._try_submit_pending_append_days(
    client=SyncTimedbJobStore(""),
    cap=4,
    inflight={},
    claims={},
    archive_pool=pool,
    tgz_archive_dir=str(tmp_path),
    batch_size=1,
    allow_partial=True,
  )


def test_batch_is_reserved_before_the_worker_can_write(monkeypatch, tmp_path):
  submit = _one_parked_day(monkeypatch, tmp_path, restore=GIB)
  at_submit = []

  class _Pool:
    def apply_async(self, fn, args):
      at_submit.append(sp.reserved_bytes())
      return object()

  assert submit(_Pool()) == 1
  assert at_submit == [GIB + 1]


def test_failed_submit_releases_its_reservation(monkeypatch, tmp_path):
  submit = _one_parked_day(monkeypatch, tmp_path, restore=GIB)

  class _Pool:
    def apply_async(self, fn, args):
      raise RuntimeError("pool closed")

  with pytest.raises(RuntimeError):
    submit(_Pool())
  assert sp.reserved_bytes() == 0


def test_parked_raw_sizes_are_read_once(monkeypatch, tmp_path):
  """F10: the gate asked for every parked file's size on every tick."""
  from hpcperfstats.dbload.lib import sync_timedb_append_day_lists as adl

  lists = adl.AppendDayClaimLists()
  for i in range(3):
    raw = tmp_path / f"r{i}"
    raw.write_bytes(b"x" * (i + 1))
    lists.add("2026-09-01", _claim(str(raw)))
  reads = []
  real = adl.os.path.getsize
  monkeypatch.setattr(
    adl.os.path, "getsize", lambda p: reads.append(p) or real(p)
  )
  assert lists.batch_bytes("2026-09-01", 3) == 6
  assert lists.batch_bytes("2026-09-01", 3) == 6
  assert len(reads) == 3
  lists.pop_batch("2026-09-01", 1)
  assert lists.batch_bytes("2026-09-01", 5) == 5
  assert len(reads) == 3
  assert set(lists._sizes) == {str(tmp_path / "r1"), str(tmp_path / "r2")}


def test_append_drain_releases_the_reservation():
  src = inspect.getsource(qo._drain_append_ready)
  assert "space.release(" in src


def test_day_close_restore_holds_a_reservation(monkeypatch, tmp_path):
  tar, _zst = _sealed_day(tmp_path)
  seen = []
  real = ah.decompress_compressed_to_tar

  def spy(*a, **k):
    seen.append(sp.reserved_bytes())
    return real(*a, **k)

  monkeypatch.setattr(ah, "decompress_compressed_to_tar", spy)
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  assert ah.ensure_daily_tar_restored_for_append(
    str(tar), 1, space_gate="finish"
  )
  assert seen and seen[0] > 0
  assert sp.reserved_bytes() == 0


def test_try_submit_none_means_blocked(monkeypatch, tmp_path):
  """Review #8: None passed in is 'appends wait', not 'compute here'."""
  qo.reset_append_day_lists_for_tests()
  raw = tmp_path / "raw-2026-09-01"
  raw.write_bytes(b"r")
  qo._APPEND_DAY_LISTS.add("2026-09-01", _claim(str(raw)))
  _set(monkeypatch, threshold=0, free=TIB)

  class _Pool:
    def apply_async(self, fn, args):
      raise AssertionError("submitted while blocked")

  assert (
    qo._try_submit_pending_append_days(
      client=SyncTimedbJobStore(""),
      cap=4,
      inflight={},
      claims={},
      archive_pool=_Pool(),
      tgz_archive_dir=str(tmp_path),
      batch_size=1,
      allow_partial=True,
      allowed_day=None,
    )
    == 0
  )


# --- Freeze under pressure (hpcperfstats01 2026-11-11) ---------------------
# Clearing 2T ended pressure (2X), parallel append restores expanded eight
# sealed days (~1.7T) within hours, and pressure came back with every append
# refused restore_slot_busy. Under pressure nothing may grow the disk but a
# seal and a reflink clone append into a tar that already exists.


def _dup_tar(path, sizes):
  with tarfile.open(path, "w") as tf:
    for n in sizes:
      info = tarfile.TarInfo("h/dup")
      info.size = n
      tf.addfile(info, io.BytesIO(b"a" * n))


@pytest.mark.parametrize("mode", ["finish", "growth"])
def test_pressure_refuses_every_day_close_and_ingest_restore(
  monkeypatch, tmp_path, mode
):
  """No restore slot under pressure: day-close (finish) restores wait too."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: GIB)
  tar = str(tmp_path / "2026-09-04.tar")
  assert sp.admit_restore(tar, mode) is False
  assert sp.restore_slot_free(tar)  # nothing was claimed
  _set(monkeypatch, threshold=TIB, free=3 * TIB)  # pressure over
  assert sp.admit_restore(tar, mode) is True


def test_pressure_refuses_append_restore_even_with_a_free_slot(
  monkeypatch, tmp_path
):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: GIB)
  qo.reset_append_day_lists_for_tests()
  raw = tmp_path / "raw"
  raw.write_bytes(b"r")
  qo._APPEND_DAY_LISTS.add("2026-09-04", _claim(str(raw)))
  tar = str(tmp_path / "2026-09-04.tar")
  assert sp.restore_slot_free(tar)
  assert qo._append_batch_need("2026-09-04", tar, 1) is None
  qo.reset_append_day_lists_for_tests()


def test_pressure_selects_only_clone_appends_to_existing_tars(
  monkeypatch, tmp_path
):
  """Restore and copying days never get a slot under pressure."""
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  monkeypatch.setattr(
    qo.tca, "clone_capable", lambda tar, raw: "09-01" in tar or "09-02" in tar
  )
  monkeypatch.setattr(
    sp,
    "select_append_day",
    lambda *a, **k: pytest.fail("gated days must not be selected"),
  )
  daily = _parked_days(
    monkeypatch,
    tmp_path,
    days={
      "2026-09-01": "open",  # clone into open tar: allowed
      "2026-09-02": "new",  # clone into a new tar: allowed
      "2026-09-03": "sealed",  # restore: refused
      "2026-09-04": "open",  # copying site: refused
    },
  )
  assert qo._select_append_day({}, daily, 256) == frozenset(
    {"2026-09-01", "2026-09-02"}
  )
  qo.reset_append_day_lists_for_tests()


def test_pressure_with_only_gated_days_claims_nothing_new_to_submit(
  monkeypatch, tmp_path
):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  daily = _parked_days(monkeypatch, tmp_path, days={"2026-09-03": "sealed"})
  assert qo._select_append_day({}, daily, 256) == frozenset()
  qo.reset_append_day_lists_for_tests()


@pytest.mark.parametrize(
  "op",
  [
    "union_rebuild",
    "tar_salvage",
    "restore_corrupt_tar",
    "repair_extract_recreate",
    "dedupe",
  ],
)
def test_pressure_refuses_tar_rewrites_but_not_the_seal(monkeypatch, op):
  monkeypatch.setattr(ah, "free_bytes_for_path", lambda _p: 100 * TIB)
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert ah.archive_space_admit("/d/2026-09-01.tar", GIB, op=op) is False
  assert ah.archive_space_admit("/d/2026-09-01.tar", GIB, op="seal") is True
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  assert ah.archive_space_admit("/d/2026-09-01.tar", GIB, op=op) is True


def test_pressure_skips_the_dedupe_rewrite(monkeypatch, tmp_path):
  tar = tmp_path / "2026-09-01.tar"
  _dup_tar(tar, [10, 20])
  before = tar.read_bytes()
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert (
    ah.dedupe_tar_keep_largest_file_per_member(str(tar), log_fn=None) is False
  )
  assert tar.read_bytes() == before
  assert not (tmp_path / "2026-09-01.tar.dedupe.tmp").exists()
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  monkeypatch.setattr(ah, "free_bytes_for_path", lambda _p: 100 * TIB)
  assert ah.dedupe_tar_keep_largest_file_per_member(str(tar), log_fn=None)
  with tarfile.open(tar) as tf:
    assert [m.size for m in tf.getmembers()] == [20]


def test_dedupe_rewrite_keeps_the_hard_reserve(monkeypatch, tmp_path):
  """A duplicate-carrying tar is copied only when the copy fits (A16)."""
  tar = tmp_path / "2026-09-01.tar"
  _dup_tar(tar, [10, 20])
  before = tar.read_bytes()
  _set(monkeypatch, threshold=0, free=0)
  monkeypatch.setattr(ah, "free_bytes_for_path", lambda _p: GIB)  # = reserve
  assert (
    ah.dedupe_tar_keep_largest_file_per_member(str(tar), log_fn=None) is False
  )
  assert tar.read_bytes() == before


def test_pressure_defers_open_tar_repair_without_touching_it(
  monkeypatch, tmp_path
):
  tar = tmp_path / "2026-09-01.tar"
  tar.write_bytes(_tar_bytes(4096)[:2048])  # truncated
  before = tar.read_bytes()
  monkeypatch.setattr(
    ah, "probe_tar_archive_readability", lambda _t: ah.CORRUPT
  )
  for name in (
    "_salvage_open_tar_for_ensure",
    "repair_truncated_daily_tar_in_place",
    "replace_corrupt_tar_from_compressed_backup",
  ):
    monkeypatch.setattr(
      ah, name, lambda *a, _n=name, **k: pytest.fail(f"{_n} under pressure")
    )
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  got = ah.ensure_open_daily_tar_readable(str(tar), caller="test", log_fn=None)
  assert (got.readable, got.transient, got.transient_reason) == (
    False,
    True,
    "env:ENOSPC",
  )
  assert tar.read_bytes() == before
