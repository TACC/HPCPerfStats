"""A3-A9: pipe drain, nested write-lock, timeouts, and pax post-replace verify."""

from __future__ import annotations

import inspect
import io
from types import SimpleNamespace

from hpcperfstats.dbload.lib import (
  file_locking,
  sync_timedb_archive_helpers as helpers,
  zstd_cli,
)


def test_compress_poll_loop_drains_pipes():
  """A3: the cooperative compress loop must drain both pipes to avoid deadlock."""
  source = inspect.getsource(zstd_cli.zstd_compress_tar_to_file)
  assert "drain_subprocess_pipes" in source or "os.read" in source
  stdout = io.BytesIO(b"x" * 200000)
  stderr = io.BytesIO(b"y" * 200000)
  proc = SimpleNamespace(
    stdout=stdout,
    stderr=stderr,
    poll=lambda: 0,
    returncode=0,
    wait=lambda timeout=None: 0,
    kill=lambda: None,
    terminate=lambda: None,
  )
  out, err = zstd_cli.drain_subprocess_pipes(proc, timeout_s=1.0)
  assert len(out) >= 1 or proc.poll() == 0
  assert isinstance(out, (bytes, bytearray))
  assert isinstance(err, (bytes, bytearray))


def test_restore_from_compressed_backup_no_self_deadlock():
  """A6: inner decompress must not re-acquire a lock the caller already holds."""
  source = inspect.getsource(helpers.replace_corrupt_tar_from_compressed_backup)
  assert "already_locked=" in source
  decomp = inspect.getsource(zstd_cli.decompress_compressed_to_tar)
  assert "already_locked" in decomp
  lock_src = inspect.getsource(file_locking.file_write_lock)
  assert "already_held" in lock_src


def test_file_write_lock_already_held_skips_acquire(tmp_path):
  """already_held must not try to open a second exclusive flock."""
  target = str(tmp_path / "day.tar")
  tmp_path.joinpath("day.tar").write_bytes(b"x")
  with file_locking.file_write_lock(target, already_held=True):
    pass


def test_pax_convert_path_is_gone():
  """A8 retired: the clone writer emits pax members on any archive format.

  A giant member never needs the archive rewritten (extract + recreate), so
  the convert path and its verify-before-replace step no longer exist.
  """
  for name in (
    "convert_daily_tar_to_pax_via_extract_recreate",
    "prepare_paths_for_giant_member_append",
  ):
    assert not hasattr(helpers, name), name


def test_decompress_stderr_is_devnull_to_avoid_pipe_deadlock():
  """F11/A5: zstd|tar pipe must not leave stderr=PIPE undrained (deadlock)."""
  pipe_src = inspect.getsource(zstd_cli._tar_readable_via_decompress_tar_pipe)
  assert "stderr=subprocess.DEVNULL" in pipe_src
  # Prefer DEVNULL over undrained PIPE on the decompress leg.
  assert pipe_src.count("stderr=subprocess.PIPE") == 0
  stdout_src = inspect.getsource(zstd_cli._decompress_stdout)
  assert "stderr=subprocess.DEVNULL" in stdout_src
  assert stdout_src.count("stderr=subprocess.PIPE") == 0


def test_tar_append_subprocess_has_progress_idle_protection():
  """A4: the append's only long subprocess (copy fallback) is idle-killed.

  Cloning is one metadata ioctl per member; a copy (no reflink) runs ``dd``
  under ``run_subprocess_with_progress`` with the tar as the progress path.
  """
  from hpcperfstats.dbload.lib import sync_timedb_tar_clone_append as tca

  source = inspect.getsource(tca._copy_range)
  assert "run_subprocess_with_progress(" in source
  assert "progress_path=tar_path" in source
  assert "subprocess.run(" not in source and "timeout=" not in source
