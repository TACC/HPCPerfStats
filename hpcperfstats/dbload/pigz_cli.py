"""Run pigz decompress with stdout/stderr logging (no Django)."""
from __future__ import annotations

import subprocess

from hpcperfstats.print_utils import log_print


def pigz_decompress_verbose(gz_path: str, thread_count: int) -> subprocess.CompletedProcess:
  """Run ``pigz -v -d`` on ``gz_path``; log stdout/stderr; non-zero raises CalledProcessError."""
  result = subprocess.run(
      ["/usr/bin/pigz", "-v", "-d", "-p", str(thread_count), gz_path],
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
