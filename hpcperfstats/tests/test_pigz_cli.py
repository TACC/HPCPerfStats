"""Tests for hpcperfstats.dbload.pigz_cli (no Django)."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from hpcperfstats.dbload.pigz_cli import pigz_decompress_verbose


def test_pigz_decompress_verbose_invokes_pigz_and_logs(monkeypatch):
  captured = []

  def _fake_run(cmd, capture_output, text, check):
    captured.append(
        {"cmd": cmd, "capture_output": capture_output, "text": text, "check": check}
    )
    return subprocess.CompletedProcess(cmd, 0, stdout="out\n", stderr="err\n")

  with patch("hpcperfstats.dbload.pigz_cli.subprocess.run", side_effect=_fake_run):
    with patch("hpcperfstats.dbload.pigz_cli.log_print") as mock_log:
      pigz_decompress_verbose("/tmp/x.gz", 4)

  assert captured[0]["cmd"] == ["/usr/bin/pigz", "-v", "-d", "-p", "4", "/tmp/x.gz"]
  assert mock_log.call_args_list[0][0][0] == "out\n"
  assert mock_log.call_args_list[1][0][0] == "err\n"


def test_pigz_decompress_verbose_raises_on_nonzero():
  with patch("hpcperfstats.dbload.pigz_cli.subprocess.run") as mock_run:
    mock_run.return_value = subprocess.CompletedProcess(
        ["/usr/bin/pigz"], 1, stdout="", stderr="bad"
    )
    with pytest.raises(subprocess.CalledProcessError):
      pigz_decompress_verbose("/a.gz", 2)
