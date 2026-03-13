"""Shared shutdown flag and interruptible sleep for dbload and analysis scripts."""
import time

from hpcperfstats.print_utils import log_print

# Mutable container so handler and callers see the same flag across modules.
shutdown_requested = [False]

def sleep_until_shutdown(seconds, interval=5):
  """Sleep for up to seconds, returning early if shutdown_requested[0] is True.
  interval: seconds between checks.
  """
  elapsed = 0
  while elapsed < seconds and not shutdown_requested[0]:
    time.sleep(min(interval, seconds - elapsed))
    elapsed += interval
