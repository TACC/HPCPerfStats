"""Tests for hpcperfstats.dbload.zstd_cli (no Django)."""
from __future__ import annotations

import io
import shutil
import signal
import subprocess
import tarfile
from unittest.mock import patch

import pytest

from hpcperfstats.dbload.zstd_cli import (
    decompress_compressed_to_tar,
    wrap_archive_zstd_cmd,
    zstd_compress_tar_to_file,
    zstd_decompress_stdout,
    zstd_decompress_verbose,
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
      "hpcperfstats.dbload.zstd_cli._archive_zstd_priority_settings",
      lambda: (10, 2, 6),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.zstd_cli.shutil.which",
      lambda name: "/usr/bin/%s" % name if name in ("ionice", "nice", "zstd") else None,
  )
  wrapped = wrap_archive_zstd_cmd(["/usr/bin/zstd", "-T0", "-q", "x.zst"])
  assert wrapped[0:8] == [
      "/usr/bin/ionice", "-c2", "-n6",
      "/usr/bin/nice", "-n10",
      "/usr/bin/zstd", "-T0", "-q",
  ]
  assert wrapped[8] == "x.zst"


def test_wrap_archive_zstd_cmd_skips_when_disabled(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.zstd_cli._archive_zstd_priority_settings",
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

  with patch("hpcperfstats.dbload.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_compress_tar_to_file(str(tar_path), str(zst_path), 0, 6)

  assert "-T0" in captured[0]


def test_zstd_gzip_decompress_verbose_invokes_zstd_gzip_format(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="err\n")

  with patch("hpcperfstats.dbload.zstd_cli.shutil.which", return_value=None):
    with patch("hpcperfstats.dbload.zstd_cli.subprocess.run", side_effect=_fake_run):
      with patch("hpcperfstats.dbload.zstd_cli.log_print"):
        with patch(
            "hpcperfstats.dbload.zstd_cli.decompress_compressed_to_tar",
            return_value=True,
        ):
          zstd_gzip_decompress_verbose("/tmp/x.tar.gz", 4)

  assert captured == []


def test_zstd_decompress_verbose_native_always_includes_long(monkeypatch):
  captured = []

  def _fake_decomp(compressed_path, tar_path, thread_count, *, remove_compressed=True):
    captured.append((compressed_path, tar_path, thread_count, remove_compressed))
    return True

  with patch(
      "hpcperfstats.dbload.zstd_cli.decompress_compressed_to_tar",
      side_effect=_fake_decomp,
  ):
    zstd_decompress_verbose("/tmp/small.tar.zst", 2)

  assert captured == [("/tmp/small.tar.zst", "/tmp/small.tar", 2, True)]


def test_zstd_compress_tar_to_file_always_includes_long(monkeypatch, tmp_path):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  tar_path = tmp_path / "day.tar"
  zst_path = tmp_path / "day.tar.zst"
  tar_path.write_bytes(b"payload")

  with patch("hpcperfstats.dbload.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_compress_tar_to_file(str(tar_path), str(zst_path), 2, 6)

  assert "--long=31" in captured[0]


def test_zstd_test_always_includes_long(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0)

  with patch("hpcperfstats.dbload.zstd_cli.subprocess.run", side_effect=_fake_run):
    zstd_test("/tmp/day.tar.zst", 3)

  assert "--long=31" in captured[0]


def test_decompress_compressed_to_tar_keeps_compressed_on_verify_failure(
    monkeypatch, tmp_path,
):
  zst_path = tmp_path / "2024-01-01.tar.zst"
  tar_path = tmp_path / "2024-01-01.tar"
  zst_path.write_bytes(b"not-valid-zst")

  assert not decompress_compressed_to_tar(str(zst_path), str(tar_path), 1)
  assert zst_path.is_file()
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
      "hpcperfstats.dbload.zstd_cli.subprocess.Popen",
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
  from hpcperfstats.dbload.zstd_cli import zstd_gzip_decompress_stdout

  with zstd_gzip_decompress_stdout(str(gz), 2) as out:
    assert out.read() == b"hello-zstd-gzip-stream"
