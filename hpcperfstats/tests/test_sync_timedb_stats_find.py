"""Unit tests for fd -X GNU stat stats discovery."""
from __future__ import annotations

import os
import shutil
from datetime import datetime

import pytest

from hpcperfstats.dbload.lib import sync_timedb_stats_find as sf
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    collect_stats_files_in_range,
)

_HOST_SUFFIX = "cluster.find.test"


def _live_discover_ready() -> bool:
  try:
    sf._resolve_find_bin(None)
    sf._resolve_stat_bin(None)
  except sf.FindStatsDiscoveryError:
    return False
  return True


def _record_key(path: str) -> str:
  return os.path.normpath(os.path.realpath(path))


def _assert_fd_x_stat_contract(argv: list[str], *, stat_bin: str, printf_fmt: str) -> None:
  assert argv[0]
  assert "--threads" in argv
  assert argv[argv.index("--threads") + 1] == "4"
  assert "--no-ignore" in argv
  assert "--absolute-path" in argv
  assert "--batch-size" in argv
  assert argv[argv.index("--batch-size") + 1] == "1000"
  assert "-X" in argv
  x_idx = argv.index("-X")
  assert argv[x_idx + 1] == stat_bin
  printf_tok = argv[x_idx + 2]
  assert printf_tok.startswith("--printf=")
  fmt = printf_tok.split("=", 1)[1]
  assert fmt == printf_fmt
  assert "\0" not in fmt
  assert "%n" in fmt and "%i" in fmt
  if printf_fmt == sf.STAT_PRINTF_FORMAT:
    assert "%s" in fmt and "%Y" in fmt
  assert "-printf" not in argv
  assert "%T@" not in " ".join(argv)
  assert "-mtime" not in argv
  assert "xargs" not in argv
  assert "-x" not in argv
  assert "--exec" not in argv
  assert "--print0" not in argv
  assert "--hidden" not in argv
  assert x_idx > argv.index("--batch-size")


def test_find_stats_argv_uses_fd_x_gnu_stat():
  argv = sf.build_find_stats_argv("/archive", find_bin="fd", stat_bin="gstat")
  _assert_fd_x_stat_contract(argv, stat_bin="gstat", printf_fmt=sf.STAT_PRINTF_FORMAT)
  assert "%Y" in argv[argv.index("-X") + 2]
  assert "--exclude" in argv
  assert argv[argv.index("--exclude") + 1] == "current*"
  assert "--min-depth" in argv and argv[argv.index("--min-depth") + 1] == "2"
  assert "--max-depth" in argv and argv[argv.index("--max-depth") + 1] == "2"
  assert os.path.abspath("/archive") in argv
  assert "." in argv


def test_find_stats_argv_changed_within_mtime_days():
  argv = sf.build_find_stats_argv(
      "/archive", mtime_days=1, find_bin="fd", stat_bin="stat",
  )
  assert "--changed-within" in argv
  assert argv[argv.index("--changed-within") + 1] == "1d"
  argv_full = sf.build_find_stats_argv(
      "/archive", mtime_days=None, find_bin="fd", stat_bin="stat",
  )
  assert "--changed-within" not in argv_full


def test_find_current_inode_argv_uses_glob_current():
  argv = sf.build_find_current_inode_argv(
      "/archive", find_bin="fdfind", stat_bin="stat",
  )
  _assert_fd_x_stat_contract(
      argv, stat_bin="stat", printf_fmt=sf.STAT_CURRENT_INODE_PRINTF,
  )
  assert "--glob" in argv
  assert "current" in argv
  assert "%Y" not in argv[argv.index("-X") + 2]


def test_find_host_scoped_argv_depth_one():
  argv = sf.build_find_host_scoped_argv(
      "/archive/h.host", find_bin="fd", stat_bin="gstat",
  )
  _assert_fd_x_stat_contract(argv, stat_bin="gstat", printf_fmt=sf.STAT_PRINTF_FORMAT)
  assert argv[argv.index("--min-depth") + 1] == "1"
  assert argv[argv.index("--max-depth") + 1] == "1"
  assert os.path.abspath("/archive/h.host") in argv


def test_is_internal_archive_stats_path_dot_prefixed_host_dir():
  assert sf.is_internal_archive_stats_path(
      "/archive/.sync_timedb_day_raw_removal/2026-08-07.json",
  )
  assert sf.is_internal_archive_stats_path("/archive/.any_sidecar/file")
  host = "/archive/i614-023.vista.tacc.utexas.edu"
  assert not sf.host_dir_is_internal_for_stats_discovery(host)


def test_filter_skips_internal_sidecar_paths():
  host_suffix = ".vista.tacc.utexas.edu"
  internal = sf.FindStatsRecord(
      path="/archive/.sync_timedb_day_raw_removal/2026-08-07.json",
      mtime=1700000000.0,
      size=0,
      inode=1,
  )
  real = sf.FindStatsRecord(
      path="/archive/i614.host" + host_suffix + "/1787359835",
      mtime=1700000000.0,
      size=10,
      inode=2,
  )
  out = sf.filter_and_sort_find_records(
      [internal, real],
      host_suffix,
      "backlog",
      None,
      {},
  )
  assert [r.path for r in out] == [real.path]


def test_parse_find_printf_records_roundtrip():
  path = "/archive/h.host/12345"
  raw = (
      path.encode()
      + b"\0"
      + b"1700000000.5\0"
      + b"99\0"
      + b"4242\0"
  )
  records = sf.parse_find_printf_records(raw)
  assert len(records) == 1
  assert records[0].path == path
  assert records[0].mtime == pytest.approx(1700000000.5)
  assert records[0].size == 99
  assert records[0].inode == 4242


def test_parse_integer_stat_y_mtime():
  path = "/archive/h.host/12345"
  raw = path.encode() + b"\0" + b"1700000000\0" + b"99\0" + b"4242\0"
  records = sf.parse_find_printf_records(raw)
  assert len(records) == 1
  assert int(records[0].mtime) == 1700000000


def test_filter_skips_inode_matching_current():
  host = "/archive/n." + _HOST_SUFFIX
  active = sf.FindStatsRecord(
      path=host + "/11111", mtime=1700000000.0, size=10, inode=99
  )
  closed = sf.FindStatsRecord(
      path=host + "/22222", mtime=1700000000.0, size=10, inode=100
  )
  out = sf.filter_and_sort_find_records(
      [active, closed],
      _HOST_SUFFIX,
      "backlog",
      None,
      {host: 99},
  )
  assert [r.path for r in out] == [closed.path]


def test_find_stats_fail_closed_missing_binary():
  with pytest.raises(sf.FindStatsDiscoveryError):
    sf._resolve_find_bin("/nonexistent/fd-binary")


def test_find_stats_fail_closed_missing_gnu_stat(tmp_path):
  fake = tmp_path / "bsd-stat"
  fake.write_text("#!/bin/sh\necho 'stat: illegal option -- printf' >&2\nexit 1\n")
  fake.chmod(0o755)
  with pytest.raises(sf.FindStatsDiscoveryError) as excinfo:
    sf._resolve_stat_bin(str(fake))
  assert "stat" in str(excinfo.value).lower()


def test_find_stats_fail_closed_missing_walker(tmp_path):
  fake = tmp_path / "not-fd"
  fake.write_text("#!/bin/sh\necho 'not a walker' >&2\nexit 1\n")
  fake.chmod(0o755)
  with pytest.raises(sf.FindStatsDiscoveryError):
    sf.run_find_stats(str(tmp_path), find_bin=str(fake))


def test_resolve_find_bin_prefers_fdfind_then_fd(monkeypatch):
  old = os.environ.pop("HPCPERFSTATS_FIND_BIN", None)

  def _which_both(name: str):
    return {"fdfind": "/opt/fdfind", "fd": "/opt/fd"}.get(name)

  try:
    monkeypatch.setattr(shutil, "which", _which_both)
    assert sf._resolve_find_bin(None) == "/opt/fdfind"

    def _which_fd_only(name: str):
      return "/opt/fd" if name == "fd" else None

    monkeypatch.setattr(shutil, "which", _which_fd_only)
    assert sf._resolve_find_bin(None) == "/opt/fd"
  finally:
    if old is not None:
      os.environ["HPCPERFSTATS_FIND_BIN"] = old


def test_resolve_find_bin_never_falls_back_to_gfind(monkeypatch):
  old = os.environ.pop("HPCPERFSTATS_FIND_BIN", None)

  def _which_gfind_only(name: str):
    return "/opt/homebrew/bin/gfind" if name in ("gfind", "find") else None

  try:
    monkeypatch.setattr(shutil, "which", _which_gfind_only)
    with pytest.raises(sf.FindStatsDiscoveryError) as excinfo:
      sf._resolve_find_bin(None)
    msg = str(excinfo.value).lower()
    assert "fd" in msg or "fdfind" in msg
  finally:
    if old is not None:
      os.environ["HPCPERFSTATS_FIND_BIN"] = old


def test_collect_discovery_path_does_not_call_os_stat_for_find_fields(
    monkeypatch, tmp_path
):
  if not _live_discover_ready():
    pytest.skip("fd/fdfind and GNU stat --printf required")
  host = tmp_path / ("n." + _HOST_SUFFIX)
  host.mkdir()
  closed = host / "22222"
  closed.write_text("done")
  t = datetime(2020, 6, 15).timestamp()
  os.utime(closed, (t, t))

  real_stat = os.stat
  stat_calls = []

  def _guard_stat(path, *a, **k):
    path_s = os.fspath(path)
    if path_s.endswith("22222") or path_s.endswith("current"):
      stat_calls.append(path_s)
      raise AssertionError("discovery must not os.stat find fields: %s" % path_s)
    return real_stat(path, *a, **k)

  monkeypatch.setattr(os, "stat", _guard_stat)
  monkeypatch.setattr(os, "lstat", _guard_stat)
  result = collect_stats_files_in_range(
      str(tmp_path), datetime(2020, 6, 1), datetime(2020, 7, 1), _HOST_SUFFIX
  )
  assert any(p.endswith("22222") for p in result)
  assert stat_calls == []


def test_live_fd_x_stat_output_matches_lstat(tmp_path):
  if not _live_discover_ready():
    pytest.skip("fd/fdfind and GNU stat --printf required")
  host = tmp_path / ("n." + _HOST_SUFFIX)
  host.mkdir()
  closed = host / "22222"
  closed.write_bytes(b"closed!")
  current = host / "current"
  current.write_bytes(b"live")
  current_bak = host / "current.bak"
  current_bak.write_bytes(b"bak")
  hidden = tmp_path / ".sync_timedb_day_raw_removal"
  hidden.mkdir()
  (hidden / "2026-08-07.json").write_text("{}")
  t = datetime(2020, 6, 15).timestamp()
  os.utime(closed, (t, t))

  records = sf.run_find_stats(str(tmp_path))
  by_path = {_record_key(rec.path): rec for rec in records}
  expected_key = _record_key(str(closed))
  assert expected_key in by_path
  rec = by_path[expected_key]
  st = os.lstat(closed)
  assert int(rec.mtime) == int(st.st_mtime)
  assert rec.size == int(st.st_size)
  assert rec.inode == int(st.st_ino)
  keys = set(by_path)
  assert _record_key(str(current)) not in keys
  assert _record_key(str(current_bak)) not in keys
  assert _record_key(str(hidden / "2026-08-07.json")) not in keys

  inode_map = sf.load_current_inode_map(str(tmp_path))
  host_key = os.path.dirname(_record_key(str(current)))
  mapped = None
  for dirname, inode in inode_map.items():
    if _record_key(dirname) == host_key or os.path.normpath(dirname) == os.path.normpath(str(host)):
      mapped = inode
      break
  assert mapped == int(os.lstat(current).st_ino)
