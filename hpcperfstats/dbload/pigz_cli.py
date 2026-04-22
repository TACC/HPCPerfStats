"""Run pigz decompress with stdout/stderr logging (no Django)."""
from __future__ import annotations

import contextlib
import shutil
import subprocess
from collections.abc import Iterator
from typing import BinaryIO

from hpcperfstats.print_utils import log_print


def pigz_executable():
  """Return ``pigz`` path (``PATH`` first, else ``/usr/bin/pigz``)."""
  return shutil.which("pigz") or "/usr/bin/pigz"


def pigz_decompress_verbose(gz_path: str, thread_count: int) -> subprocess.CompletedProcess:
  """Run ``pigz -v -d`` on ``gz_path``; log stdout/stderr; non-zero raises CalledProcessError."""
  result = subprocess.run(
      [pigz_executable(), "-v", "-d", "-p", str(thread_count), gz_path],
      capture_output=True,
      text=True,
      check=False,
  )
  if result.stdout:
    log_print(result.stdout)
  if result.stderr:
    log_print(result.stderr)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )
  return result


@contextlib.contextmanager
def pigz_decompress_stdout(
    gz_path: str, thread_count: int,
) -> Iterator[BinaryIO]:
  """Run ``pigz -d -c -p <thread_count>`` on ``gz_path``; yield stdout for piping.

  Closes the pipe and ``wait()``s on pigz; non-zero exit raises
  ``CalledProcessError``. Does not log (unlike :func:`pigz_decompress_verbose`).
  """
  proc = subprocess.Popen(
      [
          pigz_executable(),
          "-d",
          "-c",
          "-p",
          str(thread_count),
          gz_path,
      ],
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
  )
  assert proc.stdout is not None
  try:
    yield proc.stdout
  finally:
    proc.stdout.close()
    rc = proc.wait()
    if rc != 0:
      raise subprocess.CalledProcessError(rc, proc.args)
