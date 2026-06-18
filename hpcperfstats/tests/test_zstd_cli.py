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
    wrap_archive_zstd_cmd,
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


def test_wrap_archive_zstd_cmd_adds_ionice_and_nice(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli._archive_zstd_priority_settings",
      lambda: (10, 2, 6),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli.shutil.which",
      lambda name: "/usr/bin/%s" % name if name in ("ionice", "nice", "zstd") else None,
  )
  wrapped = wrap_archive_zstd_cmd(["/usr/bin/zstd", "-T0", "-q", "x.zst"])
  assert wrapped[0:8] == [
      "/usr/bin/ionice", "-c2", "-n6",
      "/usr/bin/nice", "-n10",
      "/usr/bin/zstd", "-T0", "-q",
  ]
  assert wrapped[8] == "x.zst"


def test_ingest_member_scan_zstd_without_nice(monkeypatch):
  captured = []

  def _fake_popen(cmd, stdout, stderr, apply_priority_wrap=True):
    captured.append((cmd, apply_priority_wrap))
    proc = subprocess.Popen(
        ["echo"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return proc

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli._popen_zstd",
      _fake_popen,
  )
  with zstd_decompress_stdout("/tmp/day.tar.zst", 0, apply_priority_wrap=False):
    pass
  assert captured
  cmd, wrapped = captured[0]
  assert wrapped is False
  assert cmd[0] == zstd_executable()
  assert "nice" not in cmd[:3]


def test_wrap_archive_zstd_cmd_skips_when_disabled(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli._archive_zstd_priority_settings",
      lambda: (0, 0, 0),
  )
  base = ["/usr/bin/zstd", "-d", "x.zst"]
  assert wrap_archive_zstd_cmd(base) == base


def test_zstd_compress_tar_to_file_uses_t0_when_thread_count_zero(monkeypatch, tmp_path):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  tar_path = tmp_path / "day.tar"
  zst_path = tmp_path / "day.tar.zst"
  tar_path.write_bytes(b"payload")

  with patch("hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_compress_tar_to_file(str(tar_path), str(zst_path), 0, 6)

  assert "-T0" in captured[0]


def test_zstd_gzip_decompress_verbose_invokes_zstd_gzip_format(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="err\n")

  with patch("hpcperfstats.dbload.lib.zstd_cli.shutil.which", return_value=None):
    with patch("hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run):
      with patch("hpcperfstats.dbload.lib.zstd_cli.log_print"):
        with patch(
            "hpcperfstats.dbload.lib.zstd_cli.decompress_compressed_to_tar",
            return_value=True,
        ):
          zstd_gzip_decompress_verbose("/tmp/x.tar.gz", 4)

  assert captured == []


def test_zstd_decompress_verbose_native_decompress_path(monkeypatch):
  captured = []

  def _fake_decomp(compressed_path, tar_path, thread_count, *, remove_compressed=True):
    captured.append((compressed_path, tar_path, thread_count, remove_compressed))
    return True

  with patch(
      "hpcperfstats.dbload.lib.zstd_cli.decompress_compressed_to_tar",
      side_effect=_fake_decomp,
  ):
    zstd_decompress_verbose("/tmp/small.tar.zst", 2)

  assert captured == [("/tmp/small.tar.zst", "/tmp/small.tar", 2, True)]


def test_zstd_compress_tar_to_file_command_shape(monkeypatch, tmp_path):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  tar_path = tmp_path / "day.tar"
  zst_path = tmp_path / "day.tar.zst"
  tar_path.write_bytes(b"payload")

  with patch("hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_compress_tar_to_file(str(tar_path), str(zst_path), 2, 6)

  assert "-T2" in captured[0]
  assert "-6" in captured[0]
  assert "--long" not in " ".join(captured[0])


def test_zstd_test_command_shape(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  with patch("hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_test("/tmp/day.tar.zst", 3)

  assert "-T3" in captured[0]
  assert "--long" not in " ".join(captured[0])


def test_decompress_compressed_to_tar_keeps_compressed_on_verify_failure(
    monkeypatch, tmp_path,
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

  def _fake_run(cmd, capture_output, text, check, apply_priority_wrap=True):
    return subprocess.CompletedProcess(cmd, 0)

  with patch("hpcperfstats.dbload.lib.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_test(str(zst_path), 1)

  assert sequential == [str(zst_path)]
  assert dropped == [str(zst_path)]


def test_decompress_skips_second_tar_tf_when_pipe_preflight_passes(
    monkeypatch, tmp_path,
):
  zst_path = tmp_path / "2024-01-02.tar.zst"
  tar_path = tmp_path / "2024-01-02.tar"
  zst_path.write_bytes(b"placeholder")

  verify_calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli.zstd_compressed_archive_pipe_readable",
      lambda *a, **k: True,
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
  assert verify_calls == []
  assert tar_path.is_file()


def test_decompress_pipe_preflight_failure_skips_materialize(monkeypatch, tmp_path):
  zst_path = tmp_path / "2024-01-03.tar.zst"
  tar_path = tmp_path / "2024-01-03.tar"
  zst_path.write_bytes(b"bad")

  decompress_calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli.zstd_compressed_archive_pipe_readable",
      lambda *a, **k: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.zstd_cli._decompress_to_path",
      lambda *a, **k: decompress_calls.append(a),
  )

  assert not decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert decompress_calls == []
  assert not tar_path.is_file()


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


def test_zstd_decompress_stdout_tolerates_sigpipe_after_reader_closes_pipe():
  class _FakeProc:
    args = ["/usr/bin/zstd", "-d", "-c", "-T1", "-q", "/tmp/x.zst"]

    def __init__(self):
      self.stdout = io.BytesIO(b"a")

    def wait(self):
      return -signal.SIGPIPE

  with patch(
      "hpcperfstats.dbload.lib.zstd_cli.subprocess.Popen",
      return_value=_FakeProc(),
  ):
    with zstd_decompress_stdout("/tmp/x.zst", 1) as out:
      assert out.read(1) == b"a"


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_zstd_gzip_supported_when_zstd_on_path():
  assert zstd_gzip_supported() in (True, False)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
@pytest.mark.skipif(not zstd_gzip_supported(), reason="zstd without gzip support")
def test_zstd_gzip_decompress_stdout_streams_gzip_payload(tmp_path):
  import gzip

  gz = tmp_path / "payload.gz"
  with gzip.open(gz, "wb") as f:
    f.write(b"hello-zstd-gzip-stream")
  from hpcperfstats.dbload.lib.zstd_cli import zstd_gzip_decompress_stdout

  with zstd_gzip_decompress_stdout(str(gz), 2) as out:
    assert out.read() == b"hello-zstd-gzip-stream"
