"""Tests for hpcperfstats.dbload.zstd_cli (no Django)."""
from __future__ import annotations

import io
import shutil
import signal
import subprocess
from unittest.mock import patch

import pytest

from hpcperfstats.dbload.zstd_cli import (
    zstd_decompress_stdout,
    zstd_decompress_verbose,
    zstd_gzip_decompress_verbose,
    zstd_gzip_supported,
)


def test_zstd_gzip_decompress_verbose_invokes_zstd_gzip_format(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="err\n")

  with patch("hpcperfstats.dbload.zstd_cli.shutil.which", return_value=None):
    with patch("hpcperfstats.dbload.zstd_cli.subprocess.run", side_effect=_fake_run):
      with patch("hpcperfstats.dbload.zstd_cli.log_print") as mock_log:
        zstd_gzip_decompress_verbose("/tmp/x.gz", 4)

  assert captured[0][:4] == ["/usr/bin/zstd", "-d", "--format=gzip", "-v"]
  assert "-T4" in captured[0]
  assert mock_log.call_count == 2


def test_zstd_decompress_verbose_native_includes_long_when_large_file(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(cmd)
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

  with patch("hpcperfstats.dbload.zstd_cli.shutil.which", return_value=None):
    with patch("hpcperfstats.dbload.zstd_cli.zstd_use_long_for_path", return_value=True):
      with patch("hpcperfstats.dbload.zstd_cli.subprocess.run", side_effect=_fake_run):
        zstd_decompress_verbose("/tmp/big.tar.zst", 2)

  assert "--long=31" in captured[0]


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
