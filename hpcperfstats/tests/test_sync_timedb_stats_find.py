"""Unit tests for GNU find -printf stats discovery (C1–C5)."""
from __future__ import annotations

import os
import shutil
from datetime import datetime

import pytest

from hpcperfstats.dbload.lib import sync_timedb_stats_find as sf
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    collect_stats_files_in_range,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("gfind") and not os.environ.get("HPCPERFSTATS_FIND_BIN"),
    reason="GNU find (gfind / HPCPERFSTATS_FIND_BIN) required for discovery tests",
)

_HOST_SUFFIX = "cluster.find.test"


def test_find_stats_argv_includes_printf_fields():
  argv = sf.build_find_stats_argv("/archive")
  assert "-printf" in argv
  fmt = argv[argv.index("-printf") + 1]
  assert "%p" in fmt and "%T@" in fmt and "%s" in fmt and "%i" in fmt
  # Argv must carry find's \0 escapes, not embedded NUL (subprocess rejects NULs).
  assert "\0" not in fmt
  assert "\\0" in fmt or fmt.count("%") >= 4
  assert fmt == sf.FIND_PRINTF_FORMAT


def test_find_stats_argv_mtime_days():
  argv = sf.build_find_stats_argv("/archive", mtime_days=1)
  assert "-mtime" in argv
  assert argv[argv.index("-mtime") + 1] == "-1"
  argv_full = sf.build_find_stats_argv("/archive", mtime_days=None)
  assert "-mtime" not in argv_full


def test_find_stats_argv_excludes_dot_and_current():
  argv = sf.build_find_stats_argv("/archive")
  joined = " ".join(argv)
  assert "!" in argv
  assert ".*" in argv
  assert "current*" in argv
  assert "-name" in argv
  assert ".*" in joined and "current*" in joined
  assert "-prune" in argv
  prune_name_idx = argv.index("-prune") - 1
  assert argv[prune_name_idx] == ".*"


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
    sf._resolve_find_bin("/nonexistent/gnu-find-binary")


def test_find_stats_fail_closed_printf_unsupported(tmp_path):
  fake = tmp_path / "bsd-find"
  fake.write_text("#!/bin/sh\necho 'find: unknown primary or operator: -printf' >&2\nexit 1\n")
  fake.chmod(0o755)
  with pytest.raises(sf.FindStatsDiscoveryError) as excinfo:
    sf.run_find_stats(str(tmp_path), find_bin=str(fake))
  assert "printf" in str(excinfo.value).lower() or "GNU" in str(excinfo.value)


def test_collect_discovery_path_does_not_call_os_stat_for_find_fields(
    monkeypatch, tmp_path
):
  host = tmp_path / ("n." + _HOST_SUFFIX)
  host.mkdir()
  closed = host / "22222"
  closed.write_text("done")
  t = datetime(2020, 6, 15).timestamp()
  os.utime(closed, (t, t))

  real_stat = os.stat
  stat_calls = []

  def _guard_stat(path, *a, **k):
    # Allow pytest / pathlib internals on tmp roots; forbid stats-file discovery stats.
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


def test_resolve_find_bin_prefers_gfind_when_available():
  gfind = shutil.which("gfind")
  if not gfind:
    pytest.skip("gfind not on PATH")
  # Clear env override for this check.
  old = os.environ.pop("HPCPERFSTATS_FIND_BIN", None)
  try:
    resolved = sf._resolve_find_bin(None)
    assert resolved == gfind or os.path.basename(resolved) == "gfind"
  finally:
    if old is not None:
      os.environ["HPCPERFSTATS_FIND_BIN"] = old
