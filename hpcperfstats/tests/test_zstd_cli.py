"""Tests for hpcperfstats.dbload.lib.zstd_cli (no Django)."""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import tarfile
from unittest.mock import patch

import pytest

from hpcperfstats.dbload.lib.zstd_cli import (
  decompress_compressed_to_tar,
  drop_page_cache_for_fd,
  drop_page_cache_for_paths,
  zstd_compress_tar_to_file,
  zstd_decompress_stdout,
  zstd_decompress_verbose,
  zstd_drop_page_cache_for_paths,
  zstd_executable,
  zstd_gzip_decompress_verbose,
  zstd_gzip_supported,
  zstd_test,
  zstd_thread_cli_args,
)


def test_zstd_thread_cli_args_zero_uses_t0():
  assert zstd_thread_cli_args(0) == ["-T0"]


def test_zstd_thread_cli_args_positive():
  assert zstd_thread_cli_args(4) == ["-T4"]


def test_archive_zstd_never_runs_under_nice_or_ionice(monkeypatch, tmp_path):
  """Seal, test and stream zstd run as plain zstd, never nice/ionice.

  hpcperfstats01 2026-11-02: four multi-TB seals under ``ionice -c2 -n6`` and
  ``nice -n10`` ran 9+ hours on a saturated shared volume while the space they
  free only comes back when the seal ends; the wrapper was removed.
  """
  import hpcperfstats.dbload.lib.zstd_cli as zc

  monkeypatch.setattr(zc.shutil, "which", lambda name: f"/usr/bin/{name}")
  argvs = []
  real_popen = subprocess.Popen

  def _fake_run(cmd, *args, **kwargs):
    argvs.append(list(cmd))
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

  def _fake_popen(cmd, *args, **kwargs):
    argvs.append(list(cmd))
    return real_popen(
      ["echo"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )

  tar_path = tmp_path / "day.tar"
  tar_path.write_bytes(b"payload")
  with (
    patch.object(zc.subprocess, "run", side_effect=_fake_run),
    patch.object(zc.subprocess, "Popen", side_effect=_fake_popen),
  ):
    zstd_compress_tar_to_file(
      str(tar_path), str(tmp_path / "day.tar.zst"), 0, 7
    )
    zstd_test(str(tmp_path / "day.tar.zst"), 0)
    with zstd_decompress_stdout(str(tmp_path / "day.tar.zst"), 0):
      pass
  assert len(argvs) == 3
  for argv in argvs:
    assert argv[0] == zstd_executable()
    assert not {"/usr/bin/nice", "/usr/bin/ionice", "nice", "ionice"} & set(
      argv
    )
  for gone in (
    "wrap_archive_zstd_cmd",
    "_wrap_zstd_cmd",
    "_maybe_wrap_zstd_cmd",
    "_archive_zstd_priority_settings",
  ):
    assert not hasattr(zc, gone)


def test_zstd_compress_tar_to_file_uses_t0_when_thread_count_zero(
  monkeypatch, tmp_path
):
  captured = []

  def _fake_run(cmd, *args, **kwargs):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  tar_path = tmp_path / "day.tar"
  zst_path = tmp_path / "day.tar.zst"
  tar_path.write_bytes(b"payload")

  with patch(
    "hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run
  ):
    zstd_compress_tar_to_file(str(tar_path), str(zst_path), 0, 6)

  assert "-T0" in captured[0]


def test_zstd_gzip_decompress_verbose_invokes_zstd_gzip_format(monkeypatch):
  captured = []

  def _fake_run(cmd, *args, **kwargs):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="err\n")

  with (
    patch("hpcperfstats.dbload.lib.zstd_cli.shutil.which", return_value=None),
    patch(
      "hpcperfstats.dbload.lib.zstd_cli.subprocess.run",
      side_effect=_fake_run,
    ),
    patch("hpcperfstats.dbload.lib.zstd_cli.log_print"),
    patch(
      "hpcperfstats.dbload.lib.zstd_cli.decompress_compressed_to_tar",
      return_value=True,
    ),
  ):
    zstd_gzip_decompress_verbose("/tmp/x.tar.gz", 4)

  assert captured == []


def test_zstd_decompress_verbose_native_decompress_path(monkeypatch):
  captured = []

  def _fake_decomp(
    compressed_path, tar_path, thread_count, *, remove_compressed=True
  ):
    captured.append(
      (compressed_path, tar_path, thread_count, remove_compressed)
    )
    return True

  with patch(
    "hpcperfstats.dbload.lib.zstd_cli.decompress_compressed_to_tar",
    side_effect=_fake_decomp,
  ):
    zstd_decompress_verbose("/tmp/small.tar.zst", 2)

  assert captured == [("/tmp/small.tar.zst", "/tmp/small.tar", 2, True)]


def test_zstd_compress_tar_to_file_command_shape(monkeypatch, tmp_path):
  captured = []

  def _fake_run(cmd, *args, **kwargs):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  tar_path = tmp_path / "day.tar"
  zst_path = tmp_path / "day.tar.zst"
  tar_path.write_bytes(b"payload")

  with patch(
    "hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run
  ):
    zstd_compress_tar_to_file(str(tar_path), str(zst_path), 2, 6)

  assert "-T2" in captured[0]
  assert "-6" in captured[0]
  assert "--long" not in " ".join(captured[0])


def test_zstd_test_command_shape(monkeypatch):
  captured = []

  def _fake_run(cmd, *args, **kwargs):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  with patch(
    "hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run
  ):
    zstd_test("/tmp/day.tar.zst", 3)

  assert "-T3" in captured[0]
  assert "--long" not in " ".join(captured[0])


def test_decompress_compressed_to_tar_keeps_compressed_on_verify_failure(
  monkeypatch,
  tmp_path,
):
  zst_path = tmp_path / "2024-01-01.tar.zst"
  tar_path = tmp_path / "2024-01-01.tar"
  zst_path.write_bytes(b"not-valid-zst")

  assert not decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert zst_path.is_file()
  assert not tar_path.is_file()


def test_page_cache_hints_noop_off_linux(monkeypatch, tmp_path):
  path = tmp_path / "x.zst"
  path.write_bytes(b"x")
  open_calls = []

  monkeypatch.setattr(sys, "platform", "darwin")
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._page_cache_hints_enabled",
    lambda: False,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli.os.open",
    lambda *a, **k: open_calls.append(a) or 1,
  )
  zstd_drop_page_cache_for_paths(str(path))
  assert open_calls == []


def test_drop_page_cache_for_fd_invokes_posix_fadvise(monkeypatch):
  advised = []

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._page_cache_hints_enabled",
    lambda: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli.os.posix_fadvise",
    lambda fd, off, ln, adv: advised.append((fd, off, ln, adv)),
  )
  drop_page_cache_for_fd(7, 4096, 1024)
  assert advised == [(7, 4096, 1024, os.POSIX_FADV_DONTNEED)]


def test_drop_page_cache_alias_matches_primary(monkeypatch, tmp_path):
  path = tmp_path / "stats.raw"
  path.write_bytes(b"x")
  dropped = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._page_cache_hints_enabled",
    lambda: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._advise_drop_cache",
    lambda p: dropped.append(p),
  )
  drop_page_cache_for_paths(str(path))
  zstd_drop_page_cache_for_paths(str(path))
  assert dropped == [str(path), str(path)]


def test_page_cache_hints_invoke_fadvise_on_linux(monkeypatch, tmp_path):
  path = tmp_path / "day.tar.zst"
  path.write_bytes(b"z")
  dropped = []

  monkeypatch.setattr(sys, "platform", "linux")
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._page_cache_hints_enabled",
    lambda: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._advise_drop_cache",
    lambda p: dropped.append(p),
  )
  zstd_drop_page_cache_for_paths(str(path))
  assert dropped == [str(path)]


def test_page_cache_hints_disabled_when_ini_off(monkeypatch, tmp_path):
  path = tmp_path / "day.tar.zst"
  path.write_bytes(b"z")
  open_calls = []

  monkeypatch.setattr(sys, "platform", "linux")
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._page_cache_hints_enabled",
    lambda: False,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli.os.open",
    lambda *a, **k: open_calls.append(a) or 1,
  )
  zstd_drop_page_cache_for_paths(str(path))
  assert open_calls == []


def test_zstd_test_applies_page_cache_hints_on_linux(monkeypatch, tmp_path):
  zst_path = tmp_path / "probe.tar.zst"
  zst_path.write_bytes(b"not-real")

  sequential = []
  dropped = []

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._advise_sequential_read",
    lambda p: sequential.append(p),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._advise_drop_cache",
    lambda p: dropped.append(p),
  )

  def _fake_run(cmd, *args, **kwargs):
    return subprocess.CompletedProcess(cmd, 0)

  with patch(
    "hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run
  ):
    zstd_test(str(zst_path), 1)

  assert sequential == [str(zst_path)]
  assert dropped == [str(zst_path)]


def test_decompress_verifies_tmp_before_replace_no_pipe_preflight(
  monkeypatch,
  tmp_path,
):
  zst_path = tmp_path / "2024-01-02.tar.zst"
  tar_path = tmp_path / "2024-01-02.tar"
  zst_path.write_bytes(b"placeholder")

  pipe_calls = []
  verify_calls = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli.zstd_compressed_archive_pipe_readable",
    lambda *a, **k: pipe_calls.append(a) or True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._verify_uncompressed_tar_readable",
    lambda p: verify_calls.append(p) or True,
  )

  def _fake_decompress(compressed_path, output_path, thread_count):
    with open(output_path, "wb") as f:
      f.write(b"tar-bytes")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
    _fake_decompress,
  )

  assert decompress_compressed_to_tar(
    str(zst_path),
    str(tar_path),
    1,
    remove_compressed=False,
  )
  assert pipe_calls == []
  assert len(verify_calls) == 1
  assert verify_calls[0].endswith(".decomp.tmp")
  assert tar_path.is_file()


def test_decompress_tmp_verify_failure_skips_replace(monkeypatch, tmp_path):
  zst_path = tmp_path / "2024-01-03.tar.zst"
  tar_path = tmp_path / "2024-01-03.tar"
  zst_path.write_bytes(b"bad")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._verify_uncompressed_tar_readable",
    lambda p: False,
  )

  def _fake_decompress(compressed_path, output_path, thread_count):
    with open(output_path, "wb") as f:
      f.write(b"bad-tar")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
    _fake_decompress,
  )

  assert not decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert not tar_path.is_file()
  assert zst_path.is_file()
  assert not (tmp_path / "2024-01-03.tar.decomp.tmp").exists()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_decompress_compressed_to_tar_round_trip_removes_zst(tmp_path):
  tar_path = tmp_path / "2024-01-02.tar"
  zst_path = tmp_path / "2024-01-02.tar.zst"
  member = tmp_path / "m.txt"
  member.write_text("ok")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="m.txt")
  zstd_compress_tar_to_file(str(tar_path), str(zst_path), 1, 6)
  tar_path.unlink()

  assert decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert tar_path.is_file()
  assert not zst_path.is_file()


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_decompress_compressed_to_tar_invalidates_members_pre_and_post(
  monkeypatch,
  tmp_path,
):
  """Successful sealed->tar restore must invalidate pre- and post-identity caches."""
  tar_path = tmp_path / "2024-01-04.tar"
  zst_path = tmp_path / "2024-01-04.tar.zst"
  member = tmp_path / "m.txt"
  member.write_text("ok")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(member), arcname="m.txt")
  zstd_compress_tar_to_file(str(tar_path), str(zst_path), 1, 6)
  tar_path.unlink()

  invalidated = []

  def _capture(path, **kw):
    invalidated.append((path, kw.get("reason")))

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".invalidate_after_daily_tar_mutation",
    _capture,
  )

  assert decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert invalidated == [
    (str(zst_path), "tar_restore_pre"),
    (str(tar_path), "tar_restore"),
  ]
  assert tar_path.is_file()
  assert not zst_path.is_file()


def test_decompress_compressed_to_tar_failure_does_not_invalidate(
  monkeypatch,
  tmp_path,
):
  """Failed decompress must not touch membership caches."""
  zst_path = tmp_path / "2024-01-05.tar.zst"
  tar_path = tmp_path / "2024-01-05.tar"
  zst_path.write_bytes(b"not-valid-zst")
  invalidated = []

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".invalidate_after_daily_tar_mutation",
    lambda path, **kw: invalidated.append((path, kw.get("reason"))),
  )

  assert not decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert invalidated == []
  assert zst_path.is_file()
  assert not tar_path.is_file()


def test_zstd_decompress_stdout_tolerates_sigpipe_after_reader_closes_pipe():
  class _FakeProc:
    args = ["/usr/bin/zstd", "-d", "-c", "-T1", "-q", "/tmp/x.zst"]

    def __init__(self):
      self.stdout = io.BytesIO(b"a")

    def wait(self):
      return -signal.SIGPIPE

  with (
    patch(
      "hpcperfstats.dbload.lib.zstd_cli.subprocess.Popen",
      return_value=_FakeProc(),
    ),
    zstd_decompress_stdout("/tmp/x.zst", 1) as out,
  ):
    assert out.read(1) == b"a"


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_zstd_gzip_supported_when_zstd_on_path():
  assert zstd_gzip_supported() in (True, False)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
@pytest.mark.skipif(
  not zstd_gzip_supported(), reason="zstd without gzip support"
)
def test_zstd_gzip_decompress_stdout_streams_gzip_payload(tmp_path):
  import gzip

  gz = tmp_path / "payload.gz"
  with gzip.open(gz, "wb") as f:
    f.write(b"hello-zstd-gzip-stream")
  from hpcperfstats.dbload.lib.zstd_cli import zstd_gzip_decompress_stdout

  with zstd_gzip_decompress_stdout(str(gz), 2) as out:
    assert out.read() == b"hello-zstd-gzip-stream"


def test_concurrent_decompress_compressed_to_tar_runs_one_zstd(
  monkeypatch, tmp_path
):
  import threading
  import time

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  day = "2026-06-02"
  zst_path = tmp_path / (f"{day}.tar.zst")
  tar_path = tmp_path / (f"{day}.tar")
  zst_path.write_bytes(b"zst")
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._verify_uncompressed_tar_readable",
    lambda p: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".invalidate_after_daily_tar_mutation",
    lambda *a, **k: None,
  )
  decompress_calls = []
  hold = threading.Event()
  owner_started = threading.Event()

  def _slow_decompress(compressed_path, output_path, thread_count):
    decompress_calls.append(output_path)
    owner_started.set()
    hold.wait(timeout=5)
    with open(output_path, "wb") as f:
      f.write(b"tar")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
    _slow_decompress,
  )
  results = [None, None]

  def _worker(idx):
    results[idx] = decompress_compressed_to_tar(
      str(zst_path),
      str(tar_path),
      1,
      remove_compressed=False,
    )

  try:
    t0 = threading.Thread(target=_worker, args=(0,))
    t1 = threading.Thread(target=_worker, args=(1,))
    t0.start()
    assert owner_started.wait(timeout=2)
    t1.start()
    time.sleep(0.1)
    hold.set()
    t0.join(timeout=5)
    t1.join(timeout=5)
    assert decompress_calls == [str(tar_path) + ".decomp.tmp"]
    assert results.count(True) == 2
    assert tar_path.is_file()
  finally:
    set_process_archive_members_store(None)


def test_decompress_conflict_does_not_remove_tmp(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    try_acquire_daily_tar_restore,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  day = "2026-06-02"
  zst_path = tmp_path / (f"{day}.tar.zst")
  tar_path = tmp_path / (f"{day}.tar")
  tmp_out = tmp_path / (f"{day}.tar.decomp.tmp")
  zst_path.write_bytes(b"zst")
  tmp_out.write_bytes(b"owner-in-progress")
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  try:
    owner = try_acquire_daily_tar_restore(
      day,
      reason="missing_tar",
      caller="owner",
    )
    assert owner
    removes = []

    def _tracking_remove(path):
      removes.append(path)
      raise AssertionError("loser must not remove decomp.tmp")

    monkeypatch.setattr("os.remove", _tracking_remove)
    assert not decompress_compressed_to_tar(
      str(zst_path),
      str(tar_path),
      1,
      remove_compressed=False,
      wait_for_other_owner=False,
    )
    assert removes == []
    assert tmp_out.read_bytes() == b"owner-in-progress"
  finally:
    set_process_archive_members_store(None)


def test_decompress_no_wait_returns_false_on_conflict(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    try_acquire_daily_tar_restore,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  day = "2026-06-02"
  zst_path = tmp_path / (f"{day}.tar.zst")
  tar_path = tmp_path / (f"{day}.tar")
  zst_path.write_bytes(b"zst")
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  try:
    assert try_acquire_daily_tar_restore(
      day,
      reason="missing_tar",
      caller="owner",
    )
    called = []
    monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
      lambda *a, **k: called.append(1),
    )
    assert not decompress_compressed_to_tar(
      str(zst_path),
      str(tar_path),
      1,
      wait_for_other_owner=False,
    )
    assert called == []
  finally:
    set_process_archive_members_store(None)


def test_decompress_uses_file_lock_when_store_unset(monkeypatch, tmp_path):
  import threading

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    set_process_archive_members_store,
  )

  day = "2026-06-02"
  zst_path = tmp_path / (f"{day}.tar.zst")
  tar_path = tmp_path / (f"{day}.tar")
  zst_path.write_bytes(b"zst")
  set_process_archive_members_store(None)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._verify_uncompressed_tar_readable",
    lambda p: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".invalidate_after_daily_tar_mutation",
    lambda *a, **k: None,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".notify_daily_tar_restore_cleared",
    lambda *a, **k: None,
  )
  calls = []
  hold = threading.Event()
  started = threading.Event()

  def _slow_decompress(compressed_path, output_path, thread_count):
    calls.append(1)
    started.set()
    hold.wait(timeout=2)
    with open(output_path, "wb") as f:
      f.write(b"tar")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
    _slow_decompress,
  )
  results = []

  def _owner():
    results.append(
      decompress_compressed_to_tar(
        str(zst_path),
        str(tar_path),
        1,
        remove_compressed=False,
      )
    )

  t = threading.Thread(target=_owner)
  t.start()
  assert started.wait(timeout=2)
  assert not decompress_compressed_to_tar(
    str(zst_path),
    str(tar_path),
    1,
    remove_compressed=False,
    wait_for_other_owner=False,
  )
  hold.set()
  t.join(timeout=5)
  assert calls == [1]
  assert results == [True]


def test_decompress_compressed_to_tar_does_not_skip_empty_dest(
  monkeypatch,
  tmp_path,
):
  """Size-0 dest (mkstemp) must run decompress, not short-circuit as present."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    set_process_archive_members_store,
  )

  day = "2026-06-03"
  zst_path = tmp_path / (f"{day}.tar.zst")
  tar_path = tmp_path / (f"{day}.tar")
  zst_path.write_bytes(b"zst")
  tar_path.write_bytes(b"")
  set_process_archive_members_store(None)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._verify_uncompressed_tar_readable",
    lambda p: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".invalidate_after_daily_tar_mutation",
    lambda *a, **k: None,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
    ".notify_daily_tar_restore_cleared",
    lambda *a, **k: None,
  )
  called = []

  def _write_tmp(compressed_path, output_path, thread_count):
    del compressed_path, thread_count
    called.append(output_path)
    member = tmp_path / "m.txt"
    member.write_text("ok")
    with tarfile.open(output_path, "w") as tf:
      tf.add(str(member), arcname="m.txt")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
    _write_tmp,
  )
  assert decompress_compressed_to_tar(
    str(zst_path),
    str(tar_path),
    1,
    remove_compressed=False,
  )
  assert called
  assert tar_path.stat().st_size > 0


def test_decompress_wait_owner_empty_dest_is_not_success(monkeypatch, tmp_path):
  """Waiter must not treat an empty dest left by the owner as restore success."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    try_acquire_daily_tar_restore,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  day = "2026-06-04"
  zst_path = tmp_path / (f"{day}.tar.zst")
  tar_path = tmp_path / (f"{day}.tar")
  zst_path.write_bytes(b"zst")
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  try:
    assert try_acquire_daily_tar_restore(
      day,
      reason="missing_tar",
      caller="owner",
    )
    called = []
    monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
      lambda *a, **k: called.append(1),
    )

    def _wait(target_path, *, log_fn=None):
      del target_path, log_fn
      tar_path.write_bytes(b"")

    monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord"
      ".wait_for_daily_tar_restore_before_populate",
      _wait,
    )
    assert not decompress_compressed_to_tar(
      str(zst_path),
      str(tar_path),
      1,
      wait_for_other_owner=True,
      remove_compressed=False,
    )
    assert called == []
  finally:
    set_process_archive_members_store(None)
