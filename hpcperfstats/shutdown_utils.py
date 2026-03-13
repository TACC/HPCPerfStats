"""Shared SIGTERM handling and interruptible sleep for dbload and analysis scripts.

Scripts register a handler with register_sigterm_handler() and check
shutdown_requested[0] in loops; sleep_until_shutdown(seconds) returns early if
SIGTERM was received.
"""
import signal
import time

from hpcperfstats.print_utils import log_print

# Mutable container so handler and callers see the same flag across modules.
shutdown_requested = [False]

_DEFAULT_MESSAGE = "Received SIGTERM, will exit after current work"


def register_sigterm_handler(message=None):
  """Register SIGTERM handler that sets shutdown_requested[0] and logs message."""
  if message is None:
    message = _DEFAULT_MESSAGE

  def _handler(signum, frame):
    shutdown_requested[0] = True
    log_print(message)

  signal.signal(signal.SIGTERM, _handler)


def sleep_until_shutdown(seconds, interval=5):
  """Sleep for up to seconds, returning early if shutdown_requested[0] is True.
  interval: seconds between checks.
  """
  elapsed = 0
  while elapsed < seconds and not shutdown_requested[0]:
    time.sleep(min(interval, seconds - elapsed))
    elapsed += interval
