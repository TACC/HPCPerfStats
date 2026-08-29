"""
Progress + idle kill helpers for long sync_timedb I/O and subprocesses.

Semantic progress (byte/unit) resets the idle clock. Process liveness alone
never resets idle. Long subprocesses use Popen + poll with process-group
TERM then SIGKILL on idle stall (no absolute wall timeout).

Attributes:
  ProgressIdleError: Raised when idle-no-progress kill fires on a subprocess.
  _progress_log_last_mono: Rate-limit map for SOP progress lines.
  _PROGRESS_LOG_MIN_INTERVAL_S: Minimum seconds between SOP lines per key.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Callable

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print

_progress_log_last_mono: dict[str, float] = {}
_PROGRESS_LOG_MIN_INTERVAL_S = 30.0


class ProgressIdleError(RuntimeError):
  """
  Raised when a long subprocess makes no semantic progress for the idle window.

  Attributes:
    stage: Stall stage token (default ``idle_stall``).
    idle_s: Idle seconds observed before kill.
    path: Optional path label for operators.
  """

  def __init__(
    self,
    message: str,
    *,
    stage: str = "idle_stall",
    idle_s: float | None = None,
    path: str | None = None,
  ) -> None:
    """
    Build a progress-idle error with operator metadata.

    Args:
      message (str): Human-readable error text.
      stage (str): Stage token packed for soft-requeue paths.
      idle_s (float | None): Idle seconds before kill.
      path (str | None): Path label for logs.

    Returns:
      None

    Examples:
      >>> ProgressIdleError("x", idle_s=1.0)  # doctest: +SKIP
    """
    super().__init__(message)
    self.stage = stage
    self.idle_s = idle_s
    self.path = path


def log_progress_sop(
  *,
  stage: str,
  path: str,
  advancing: bool,
  idle_s: float,
  last_progress: float | None,
  metric: str,
  force: bool = False,
  clock: Any = time.monotonic,
) -> None:
  """
  Emit one greppable progress SOP line (rate-limited unless ``force``).

  Args:
    stage (str): Work stage token (e.g. ``tar_append``).
    path (str): Path or day label.
    advancing (bool): Whether semantic progress occurred in the last poll.
    idle_s (float): Seconds since last semantic progress.
    last_progress (float | None): Monotonic last-progress timestamp.
    metric (str): ``bytes``, ``lines``, or ``members``.
    force (bool): Bypass rate limit.
    clock (Any): Monotonic clock (injectable for tests).

  Returns:
    None

  Examples:
    >>> log_progress_sop(
    ...   stage="tar_append", path="/x", advancing=True, idle_s=0.0,
    ...   last_progress=1.0, metric="bytes", force=True,
    ... )  # doctest: +SKIP
  """
  key = "%s:%s" % (stage, path)
  now = float(clock())
  if not force:
    last = _progress_log_last_mono.get(key)
    if last is not None and (now - last) < _PROGRESS_LOG_MIN_INTERVAL_S:
      return
  _progress_log_last_mono[key] = now
  last_s = "%.3f" % float(last_progress) if last_progress is not None else "-"
  log_print(
      "progress stage=%s path=%s advancing=%s idle_s=%.1f "
      "last_progress=%s metric=%s"
      % (
          stage,
          path,
          "true" if advancing else "false",
          float(idle_s),
          last_s,
          metric,
      ),
      flush=True,
  )


def _kill_process_group(proc: subprocess.Popen) -> None:
  """
  TERM then SIGKILL a child process group; best-effort reap.

  Args:
    proc (subprocess.Popen): Child started with ``start_new_session=True``.

  Returns:
    None

  Examples:
    >>> _kill_process_group(None)  # doctest: +SKIP
  """
  if proc.poll() is not None:
    return
  pid = int(proc.pid)
  try:
    os.killpg(pid, signal.SIGTERM)
  except (ProcessLookupError, PermissionError, OSError):
    try:
      proc.terminate()
    except (ProcessLookupError, OSError):
      pass
  deadline = time.monotonic() + 5.0
  while proc.poll() is None and time.monotonic() < deadline:
    time.sleep(0.05)
  if proc.poll() is None:
    try:
      os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
      try:
        proc.kill()
      except (ProcessLookupError, OSError):
        pass
  try:
    proc.wait(timeout=5.0)
  except Exception:
    pass


def run_subprocess_with_progress(
  args: list[str],
  *,
  progress_path: str | None = None,
  stage: str = "subprocess",
  metric: str = "bytes",
  idle_s: float | None = None,
  poll_s: float = 1.0,
  progress_fn: Callable[[], int | float] | None = None,
  clock: Any = time.monotonic,
  **popen_kwargs: Any,
) -> subprocess.CompletedProcess:
  """
  Run a long subprocess with semantic progress + idle kill (no wall timeout).

  Progress is measured by ``progress_fn`` (default: size of ``progress_path``).
  Child PID aliveness alone does not reset the idle clock.

  Args:
    args (list[str]): Argv for ``Popen``.
    progress_path (str | None): File whose size growth counts as progress.
    stage (str): SOP stage token.
    metric (str): SOP metric token (``bytes`` / ``lines`` / ``members``).
    idle_s (float | None): Idle window; ``None`` uses ``sync_ingest_stall_idle_s``.
    poll_s (float): Poll interval seconds.
    progress_fn (Callable | None): Returns a monotonic progress counter.
    clock (Any): Monotonic clock (injectable for tests).
    **popen_kwargs (Any): Extra kwargs for ``Popen`` (stdout/stderr defaults).

  Returns:
    subprocess.CompletedProcess: Result after successful exit.

  Raises:
    ProgressIdleError: When no semantic progress for ``idle_s``.
    FileNotFoundError: When the executable is missing.
    Exception: Propagates unexpected child/control-flow failures.

  Examples:
    >>> run_subprocess_with_progress(["true"], idle_s=0.0)  # doctest: +SKIP
  """
  if idle_s is None:
    idle_s = float(cfg.get_sync_ingest_stall_idle_s())
  idle_s = max(0.0, float(idle_s))
  poll_s = max(0.05, float(poll_s))

  def _default_progress() -> int:
    """
    Measure progress from ``progress_path`` size (0 when missing).

    Returns:
      int: File size in bytes, or 0.

    Examples:
      >>> _default_progress()  # doctest: +SKIP
    """
    if not progress_path:
      return 0
    try:
      return int(os.path.getsize(progress_path))
    except OSError:
      return 0

  measure = progress_fn or _default_progress
  path_label = str(progress_path or (args[0] if args else "subprocess"))
  kwargs = dict(popen_kwargs)
  kwargs.setdefault("stdout", subprocess.PIPE)
  kwargs.setdefault("stderr", subprocess.PIPE)
  kwargs.setdefault("text", True)
  kwargs["start_new_session"] = True
  proc = subprocess.Popen(args, **kwargs)
  last_metric = float(measure())
  last_progress_mono = float(clock())
  try:
    while True:
      rc = proc.poll()
      now = float(clock())
      cur = float(measure())
      advancing = cur > last_metric
      if advancing:
        last_metric = cur
        last_progress_mono = now
      idle_elapsed = now - last_progress_mono
      log_progress_sop(
          stage=stage,
          path=path_label,
          advancing=advancing,
          idle_s=idle_elapsed,
          last_progress=last_progress_mono,
          metric=metric,
          force=False,
          clock=clock,
      )
      if rc is not None:
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=int(rc),
            stdout=stdout,
            stderr=stderr,
        )
      if idle_s > 0.0 and idle_elapsed >= idle_s:
        _kill_process_group(proc)
        raise ProgressIdleError(
            "subprocess idle stall stage=%s path=%s idle_s=%.1f"
            % (stage, path_label, idle_elapsed),
            stage="idle_stall",
            idle_s=idle_elapsed,
            path=path_label,
        )
      time.sleep(poll_s)
  except BaseException:
    if proc.poll() is None:
      _kill_process_group(proc)
    raise
