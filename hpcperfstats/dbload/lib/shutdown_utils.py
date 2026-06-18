"""Shared shutdown helpers for dbload and analysis scripts."""
import os
import signal
import time

from hpcperfstats.dbload.lib.print_utils import log_print

# Mutable container so handler and callers see the same flag across modules.
shutdown_requested = [False]


def send_sigchld_to_parent(parent_pid=None):
  """Best-effort: notify the parent process with SIGCHLD.

  Note: SIGCHLD is typically used to report child termination, but some
  supervisors/launchers rely on it as a shutdown notification signal.
  """
  if parent_pid is None:
    parent_pid = os.getppid()
  try:
    os.kill(parent_pid, signal.SIGCHLD)
  except Exception as e:
    # Avoid failing shutdown due to missing/changed signal semantics.
    log_print("Failed to send SIGCHLD to parent: %s" % e)


def make_sigterm_handler(shutdown_flag_container, exit_code=143):
  """Create a SIGTERM handler that sets a shared shutdown flag then exits."""

  def _handler(signum, frame):
    shutdown_flag_container[0] = True
    raise SystemExit(exit_code)

  return _handler


def sleep_until_shutdown(seconds, interval=5):
  """Sleep for up to seconds, returning early if shutdown_requested[0] is True.
  interval: seconds between checks.
  """
  elapsed = 0
  while elapsed < seconds and not shutdown_requested[0]:
    time.sleep(min(interval, seconds - elapsed))
    elapsed += interval
