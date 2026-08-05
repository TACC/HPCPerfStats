"""
Shared shutdown helpers for dbload and analysis scripts.

Attributes:
  shutdown_requested: Attribute.
"""
from __future__ import annotations

from typing import Any

import os
import signal
import time

from hpcperfstats.dbload.lib.print_utils import log_print

# Mutable container so handler and callers see the same flag across modules.
shutdown_requested = [False]


def send_sigchld_to_parent(parent_pid: Any | None = None) -> None:
  """
  Best-effort: notify the parent process with SIGCHLD.
  
  Note: SIGCHLD is typically used to report child termination, but some
  supervisors/launchers rely on it as a shutdown notification signal.
  
  Args:
    parent_pid (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> send_sigchld_to_parent(None)  # doctest: +SKIP
  """
  if parent_pid is None:
    parent_pid = os.getppid()
  try:
    os.kill(parent_pid, signal.SIGCHLD)
  except Exception as e:
    # Avoid failing shutdown due to missing/changed signal semantics.
    log_print("Failed to send SIGCHLD to parent: %s" % e)


def make_sigterm_handler(
  shutdown_flag_container: Any,
  exit_code: int = 143,
) -> Any:
  """
  Create a SIGTERM handler that sets a shared shutdown flag then exits.
  
  Args:
    shutdown_flag_container (Any): Shutdown flag container passed to this
    helper.
    exit_code (int): Integer value for exit code.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> make_sigterm_handler(None, 0)  # doctest: +SKIP
  """

  def _handler(signum: Any, frame: Any) -> None:
    """
    Internal helper to handle handler.
    
    Args:
      signum (Any): Signum passed to this helper.
      frame (Any): Frame passed to this helper.
    
    Returns:
      None
    
    Raises:
      SystemExit: Raised when ``_handler`` hits a ``SystemExit`` failure path.
    
    Examples:
      >>> _handler(None, None)  # doctest: +SKIP
    """
    shutdown_flag_container[0] = True
    raise SystemExit(exit_code)

  return _handler


def sleep_until_shutdown(
  seconds: Any,
  interval: int = 5,
  on_tick: Any | None = None,
) -> None:
  """
  Sleep for up to seconds, returning early if shutdown_requested[0] is True.
  
  interval: seconds between checks.
  on_tick: optional callable invoked at the start of each interval slice
  (used by sync_timedb idle paths for throttled supervisor child hygiene).
  
  Args:
    seconds (Any): Seconds passed to this helper.
    interval (int): Integer value for interval.
    on_tick (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> sleep_until_shutdown(None, 0, None)  # doctest: +SKIP
  """
  elapsed = 0
  while elapsed < seconds and not shutdown_requested[0]:
    if on_tick is not None:
      try:
        on_tick()
      except Exception:
        pass
    time.sleep(min(interval, seconds - elapsed))
    elapsed += interval