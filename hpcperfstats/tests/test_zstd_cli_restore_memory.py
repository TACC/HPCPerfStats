"""Regression: restore verify/decompress memory contracts (OOM Sep 26)."""

from __future__ import annotations

import inspect
import subprocess
from unittest.mock import MagicMock

from hpcperfstats.dbload.lib import zstd_cli as z


def test_verify_uncompressed_tar_readable_uses_devnull_not_capture():
  """tar tf verify must not capture the full listing into memory."""
  src = inspect.getsource(z._verify_uncompressed_tar_readable)
  assert "capture_output" not in src
  assert "DEVNULL" in src


def test_decompress_to_path_does_not_dontneed_output_before_verify(
  monkeypatch, tmp_path
):
  """DONTNEED must not drop the decompress output before verify re-reads it."""
  compressed = tmp_path / "day.tar.zst"
  compressed.write_bytes(b"fake-zst")
  output = tmp_path / "day.tar.decomp.tmp"
  dropped: list[str] = []

  def _fake_run(cmd, **kwargs):
    del cmd, kwargs
    output.write_bytes(b"tar-bytes")
    return MagicMock(returncode=0, stdout="", stderr="")

  monkeypatch.setattr(z, "detect_compressed_format", lambda _p: "zst")
  monkeypatch.setattr(z, "zstd_executable", lambda: "zstd")
  monkeypatch.setattr(z, "_thread_args", lambda _n: [])
  monkeypatch.setattr(z, "_advise_sequential_read", lambda _p: None)
  monkeypatch.setattr(z, "_run_zstd", _fake_run)
  monkeypatch.setattr(
    z,
    "drop_page_cache_for_paths",
    lambda *paths: dropped.extend(str(p) for p in paths),
  )

  z._decompress_to_path(str(compressed), str(output), 1)
  assert str(compressed) in dropped
  assert str(output) not in dropped


def test_verify_runs_tar_tf_with_devnull(monkeypatch, tmp_path):
  """Live subprocess.run for verify must pass DEVNULL stdout/stderr."""
  tar_path = tmp_path / "day.tar"
  tar_path.write_bytes(b"x")
  seen = {}

  def _fake_run(cmd, **kwargs):
    seen["cmd"] = cmd
    seen["kwargs"] = kwargs
    return MagicMock(returncode=0)

  monkeypatch.setattr(z, "_tar_list_executable", lambda: "tar")
  monkeypatch.setattr(z, "_advise_sequential_read", lambda _p: None)
  monkeypatch.setattr(z, "_advise_drop_cache", lambda _p: None)
  monkeypatch.setattr(subprocess, "run", _fake_run)

  assert z._verify_uncompressed_tar_readable(str(tar_path)) is True
  assert seen["kwargs"].get("stdout") is subprocess.DEVNULL
  assert seen["kwargs"].get("stderr") is subprocess.DEVNULL
  assert "capture_output" not in seen["kwargs"]
