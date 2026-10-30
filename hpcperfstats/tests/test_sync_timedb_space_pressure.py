"""Archive free-space pressure: appends and restores yield to day close.

With ``sync_archive_space_pressure_free_bytes`` = X set, pressure starts when
the archive filesystem has less than X free and lasts until it has 2X free.
Under pressure appends wait while any day close is finishing a day (not just
checking); otherwise one day at a time (the oldest) gets appends, then that
day's close runs before the next day starts.
"""

from __future__ import annotations

import io
import subprocess
import tarfile

import pytest

from hpcperfstats.dbload.lib import (
  conf_parser as cfg,
  sync_timedb_archive_helpers as ah,
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
  sync_timedb_space_pressure as sp,
  zstd_cli,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore

GIB = 1 << 30
TIB = 1 << 40


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
  sp.reset_for_tests()
  monkeypatch.setattr(cfg, "get_sync_archive_min_free_bytes", lambda: GIB)
  yield
  sp.reset_for_tests()


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
  # Its day close finishes the day; once that ends, the next day starts.
  sp.note_day_close_stage("2026-09-01", "seal")
  assert sp.select_append_day(["2026-09-02"], set()) is None
  sp.clear_day_close("2026-09-01")
  assert sp.select_append_day(["2026-09-02"], set()) == "2026-09-02"


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


def test_fill_under_pressure_claims_then_submits_oldest_day(
  monkeypatch, tmp_path
):
  """End to end through _fill_append_slots with an empty day list."""
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
  assert submitted == ["2026-09-01.tar"]


def test_pressure_end_resumes_all_days(monkeypatch):
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert sp.select_append_day(["2026-09-01"], set()) == "2026-09-01"
  _set(monkeypatch, threshold=TIB, free=2 * TIB)
  assert sp.select_append_day(["2026-09-02"], set()) is sp.ALL_DAYS


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
  )


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
  assert ah.ensure_daily_tar_restored_for_append(str(tar), 1) is False
  assert not tar.exists()
  assert zst.read_bytes() == before


def test_day_close_restore_allowed_under_pressure(monkeypatch, tmp_path):
  tar, _zst = _sealed_day(tmp_path)
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  assert ah.ensure_daily_tar_restored_for_append(
    str(tar), 1, space_gate="finish"
  )
  assert tar.stat().st_size > 0


def test_finish_restore_still_keeps_hard_reserve(monkeypatch, tmp_path):
  tar, _zst = _sealed_day(tmp_path)
  _set(monkeypatch, threshold=TIB, free=GIB)
  assert (
    ah.ensure_daily_tar_restored_for_append(str(tar), 1, space_gate="finish")
    is False
  )
  assert not tar.exists()


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


def _submit(monkeypatch, tmp_path, days, *, free: int, restore=0):
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
  monkeypatch.setattr(sp, "restore_bytes_estimate", lambda _t: restore)
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


def test_submit_under_pressure_feeds_only_the_oldest_day(monkeypatch, tmp_path):
  got = _submit(
    monkeypatch, tmp_path, ["2026-09-02", "2026-09-01"], free=TIB // 2
  )
  assert got == ["2026-09-01.tar"]
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


def test_seal_compress_drops_priority_wrapper_under_pressure(
  monkeypatch, tmp_path
):
  seen = {}

  def fake_run(cmd, *, apply_priority_wrap=True, **_k):
    seen["wrap"] = apply_priority_wrap
    return subprocess.CompletedProcess(cmd, 0, "", "")

  monkeypatch.setattr(zstd_cli, "_run_zstd", fake_run)
  tar = tmp_path / "2026-09-07.tar"
  tar.write_bytes(b"x")
  _set(monkeypatch, threshold=TIB, free=TIB // 2)
  zstd_cli.zstd_compress_tar_to_file(str(tar), str(tar) + ".zst.tmp", 1, 3)
  assert seen["wrap"] is False
  _set(monkeypatch, threshold=TIB, free=3 * TIB)
  zstd_cli.zstd_compress_tar_to_file(str(tar), str(tar) + ".zst.tmp", 1, 3)
  assert seen["wrap"] is True
