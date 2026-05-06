"""Tests for hpcperfstats.dbload.pigz_cli (no Django)."""
from __future__ import annotations

import io
import shutil
import signal
import subprocess
from unittest.mock import patch

import pytest

from hpcperfstats.dbload.pigz_cli import pigz_decompress_stdout, pigz_decompress_verbose


def test_pigz_decompress_verbose_invokes_pigz_and_logs(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(
        {"cmd": cmd, "capture_output": capture_output, "text": text, "check": check}
    )
    return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="err\n")

  with patch("hpcperfstats.dbload.pigz_cli.shutil.which", return_value=None):
    with patch("hpcperfstats.dbload.pigz_cli.subprocess.run", side_effect=_fake_run):
      with patch("hpcperfstats.dbload.pigz_cli.log_print") as mock_log:
        pigz_decompress_verbose("/tmp/x.gz", 4)

  assert captured[0]["cmd"] == ["/usr/bin/pigz", "-v", "-d", "-p", "4", "/tmp/x.gz"]
  assert mock_log.call_args_list[0][0][0] == "out\n"
  assert mock_log.call_args_list[1][0][0] == "err\n"


def test_pigz_decompress_verbose_raises_on_nonzero():
  with patch("hpcperfstats.dbload.pigz_cli.shutil.which", return_value=None):
    with patch("hpcperfstats.dbload.pigz_cli.subprocess.run") as mock_run:
      mock_run.return_value = subprocess.CompletedProcess(
          ["/usr/bin/pigz"], 1, stdout="", stderr="bad"
      )
      with pytest.raises(subprocess.CalledProcessError):
        pigz_decompress_verbose("/a.gz", 2)


def test_pigz_decompress_stdout_tolerates_sigpipe_after_reader_closes_pipe():
  """Tar stops at end-of-archive; gzip decompressor may see SIGPIPE on stdout."""

  class _FakeProc:
    args = ["/usr/bin/pigz", "-d", "-c", "-p", "1", "/tmp/x.gz"]

    def __init__(self):
      self.stdout = io.BytesIO(b"a")

    def wait(self):
      return -signal.SIGPIPE

  with patch(
      "hpcperfstats.dbload.pigz_cli.subprocess.Popen",
      return_value=_FakeProc(),
  ):
    with pigz_decompress_stdout("/tmp/x.gz", 1) as out:
      assert out.read(1) == b"a"


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not on PATH")
def test_pigz_decompress_stdout_streams_gzip_payload(tmp_path):
  import gzip

  gz = tmp_path / "payload.gz"
  with gzip.open(gz, "wb") as f:
    f.write(b"hello-pigz-stream")
  with pigz_decompress_stdout(str(gz), 2) as out:
    assert out.read() == b"hello-pigz-stream"


@pytest.mark.skipif(not shutil.which("pigz"), reason="pigz not on PATH")
def test_pigz_decompress_stdout_raises_on_bad_gzip(tmp_path):
  bad = tmp_path / "bad.gz"
  bad.write_bytes(b"not gzip")
  with pytest.raises(subprocess.CalledProcessError):
    with pigz_decompress_stdout(str(bad), 1) as out:
      out.read()
