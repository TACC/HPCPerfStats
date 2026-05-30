"""Run zstd for daily archive compress/decompress (native .zst and --format=gzip)."""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
from collections.abc import Iterator
from typing import BinaryIO

from hpcperfstats.dbload.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    detect_compressed_format,
    zstd_long_flags,
)
from hpcperfstats.file_locking import file_write_lock
from hpcperfstats.print_utils import log_print

_GZIP_FORMAT = ("--format=gzip",)


def zstd_executable() -> str:
  return shutil.which("zstd") or "/usr/bin/zstd"


def zstd_gzip_supported() -> bool:
  """True when the zstd binary reports gzip in supported formats."""
  try:
    result = subprocess.run(
        [zstd_executable(), "-vV"],
        capture_output=True,
        text=True,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return False
  combined = (result.stdout or "") + (result.stderr or "")
  return "gzip" in combined.lower()


def _thread_args(thread_count: int) -> list[str]:
  # Combined ``-T#`` form: separate ``-T`` and ``N`` makes zstd treat ``N`` as a path.
  return ["-T%d" % max(1, int(thread_count))]


def _verify_uncompressed_tar_readable(tar_path: str) -> bool:
  tar_bin = shutil.which("tar") or "/bin/tar"
  try:
    result = subprocess.run(
        [tar_bin, "tf", tar_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0
  except (OSError, subprocess.SubprocessError):
    return False


def _decompress_to_path(
    compressed_path: str,
    output_path: str,
    thread_count: int,
) -> None:
  fmt = detect_compressed_format(compressed_path)
  cmd = [
      zstd_executable(),
      "-d",
      "-f",
      *_thread_args(thread_count),
      "-q",
      "-o",
      output_path,
  ]
  if fmt == "zst":
    cmd.extend(zstd_long_flags())
    cmd.append(compressed_path)
  elif fmt == "gz":
    cmd.extend(_GZIP_FORMAT)
    cmd.append(compressed_path)
  else:
    raise ValueError("unsupported compressed archive format: %s" % compressed_path)
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        cmd,
        output=result.stdout,
        stderr=result.stderr,
    )


def decompress_compressed_to_tar(
    compressed_path: str,
    tar_path: str,
    thread_count: int,
    *,
    remove_compressed: bool = True,
) -> bool:
  """Decompress to a verified sibling ``.tar``; unlink compressed only on success."""
  if not compressed_path or not os.path.isfile(compressed_path):
    return False
  tmp_path = "%s.decomp.tmp" % tar_path
  try:
    if os.path.exists(tmp_path):
      os.remove(tmp_path)
  except OSError:
    pass
  try:
    _decompress_to_path(compressed_path, tmp_path, thread_count)
  except (OSError, subprocess.CalledProcessError, ValueError):
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    return False
  if not _verify_uncompressed_tar_readable(tmp_path):
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    return False
  try:
    with file_write_lock(tar_path):
      os.replace(tmp_path, tar_path)
  except OSError:
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    return False
  if remove_compressed:
    try:
      with file_write_lock(compressed_path):
        if os.path.isfile(compressed_path):
          os.remove(compressed_path)
    except OSError:
      return False
  return True


def _wait_decompress_proc(proc: subprocess.Popen, args: list) -> None:
  rc = proc.wait()
  if rc != 0:
    if os.name == "posix" and rc in (-signal.SIGPIPE, 128 + signal.SIGPIPE):
      return
    raise subprocess.CalledProcessError(rc, args)


@contextlib.contextmanager
def _decompress_stdout(
    cmd: list[str],
) -> Iterator[BinaryIO]:
  proc = subprocess.Popen(
      cmd,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
  )
  assert proc.stdout is not None
  try:
    yield proc.stdout
  finally:
    proc.stdout.close()
    _wait_decompress_proc(proc, cmd)


def zstd_decompress_verbose(
    zst_path: str,
    thread_count: int,
) -> subprocess.CompletedProcess:
  """Restore sibling ``.tar`` from ``.tar.zst`` using the safe decompress helper."""
  if not zst_path.endswith(DAILY_ARCHIVE_ZST_SUFFIX):
    raise ValueError("expected .tar.zst path: %s" % zst_path)
  tar_path = zst_path[: -len(DAILY_ARCHIVE_ZST_SUFFIX)] + ".tar"
  ok = decompress_compressed_to_tar(zst_path, tar_path, thread_count)
  if not ok:
    raise subprocess.CalledProcessError(1, [zstd_executable(), "-d", zst_path])
  return subprocess.CompletedProcess([zstd_executable(), "-d", zst_path], 0)


@contextlib.contextmanager
def zstd_decompress_stdout(
    zst_path: str,
    thread_count: int,
) -> Iterator[BinaryIO]:
  cmd = [
      zstd_executable(),
      "-d",
      "-c",
      *_thread_args(thread_count),
      *zstd_long_flags(),
      "-q",
      zst_path,
  ]
  with _decompress_stdout(cmd) as stdout:
    yield stdout


def zstd_test(
    zst_path: str,
    thread_count: int,
) -> subprocess.CompletedProcess:
  cmd = [
      zstd_executable(),
      "-t",
      *_thread_args(thread_count),
      *zstd_long_flags(),
      "-q",
      zst_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
  return result


def zstd_gzip_decompress_verbose(
    gz_path: str,
    thread_count: int,
) -> subprocess.CompletedProcess:
  """Restore sibling ``.tar`` from legacy ``.tar.gz`` using the safe decompress helper."""
  if not gz_path.endswith(DAILY_ARCHIVE_GZ_SUFFIX):
    raise ValueError("expected .tar.gz path: %s" % gz_path)
  tar_path = gz_path[: -len(DAILY_ARCHIVE_GZ_SUFFIX)] + ".tar"
  ok = decompress_compressed_to_tar(gz_path, tar_path, thread_count)
  if not ok:
    raise subprocess.CalledProcessError(1, [zstd_executable(), "-d", gz_path])
  return subprocess.CompletedProcess([zstd_executable(), "-d", gz_path], 0)


@contextlib.contextmanager
def zstd_gzip_decompress_stdout(
    gz_path: str,
    thread_count: int,
) -> Iterator[BinaryIO]:
  cmd = [
      zstd_executable(),
      "-d",
      *_GZIP_FORMAT,
      "-c",
      *_thread_args(thread_count),
      "-q",
      gz_path,
  ]
  with _decompress_stdout(cmd) as stdout:
    yield stdout


def zstd_gzip_test(gz_path: str, thread_count: int) -> subprocess.CompletedProcess:
  cmd = [
      zstd_executable(),
      "-t",
      *_GZIP_FORMAT,
      *_thread_args(thread_count),
      "-q",
      gz_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
  return result


def zstd_compress_tar_to_file(
    tar_path: str,
    zst_path: str,
    thread_count: int,
    compress_level: int,
) -> None:
  """Compress ``tar_path`` to ``zst_path`` (caller manages temp/replace)."""
  try:
    tar_bytes = os.path.getsize(tar_path)
  except OSError:
    tar_bytes = 0
  cmd = [
      zstd_executable(),
      *_thread_args(thread_count),
      "-%d" % int(compress_level),
      "-q",
      "-o",
      zst_path,
      "--size-hint=%d" % int(tar_bytes),
      *zstd_long_flags(),
      tar_path,
  ]
  result = subprocess.run(cmd, capture_output=True, text=True, check=False)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        stderr=result.stderr,
    )
