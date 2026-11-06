"""
Supervisord daemon: Admin Monitor ingest coverage sweeper.

Runs ``hpcperfstats.dbload.lib.ingest_coverage.run_sweep`` once per
``[PIPELINE] ingest_coverage_sweep_interval_s`` in its own process (isolated
from listend, sync_timedb and update_metrics), on one DB connection, one host
at a time. With ``ingest_coverage_sweep_enabled = no`` it logs one line and
exits 0 before touching the DB or Valkey; supervisord
(``autorestart=unexpected``, ``exitcodes=0``) leaves it stopped.

Run: ``python -m hpcperfstats.dbload.ingest_coverage_sweeper``.

Attributes:
  SCRIPT_NAME: Process-title / log script name.
  _stop: Set by SIGTERM / SIGINT to end the loop between hosts or sleeps.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from typing import Any

SCRIPT_NAME = "ingest_coverage_sweeper.py"
_stop = threading.Event()


def _request_stop(signum: int, _frame: Any) -> None:
  """
  Signal handler: ask the sweep loop to stop.

  Args:
    signum (int): Signal number.
    _frame (Any): Interrupted frame (unused).

  Returns:
    None

  Examples:
    >>> _request_stop(15, None)
    >>> _stop.is_set()
    True
    >>> _stop.clear()
  """
  _stop.set()


def main() -> int:
  """
  Sweep forever (until SIGTERM) or exit 0 at once when disabled.

  Returns:
    int: Process exit code (0 for a clean disabled exit or a stop request).

  Examples:
    >>> main()  # doctest: +SKIP
    0
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg
  from hpcperfstats.dbload.lib.print_utils import log_print

  if not cfg.get_ingest_coverage_sweep_enabled():
    log_print(
      "ingest_coverage sweeper disabled "
      "(ingest_coverage_sweep_enabled = no); exiting",
      flush=True,
    )
    return 0

  from hpcperfstats.dbload.lib.blas_thread_env import configure_blas_thread_env

  configure_blas_thread_env()
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()
  from django.db import close_old_connections

  from hpcperfstats.dbload.lib.ingest_coverage import run_sweep, scan_host_pairs
  from hpcperfstats.dbload.lib.process_title import set_daemon_process_title
  from hpcperfstats.site.lib.machine.cache_utils import get_valkey_client
  from hpcperfstats.site.lib.machine.host_data_latest import (
    latest_sample_time_by_host,
    list_recent_host_fqdns_from_valkey,
  )

  set_daemon_process_title(name=SCRIPT_NAME, role="main")
  signal.signal(signal.SIGTERM, _request_stop)
  signal.signal(signal.SIGINT, _request_stop)
  interval_s = cfg.get_ingest_coverage_sweep_interval_s()
  while not _stop.is_set():
    started = time.monotonic()
    client = get_valkey_client()
    if client is None:
      log_print("ingest_coverage sweep: no Valkey client; retrying", flush=True)
    else:
      close_old_connections()
      try:
        run_sweep(
          client=client,
          fqdns=sorted(set(list_recent_host_fqdns_from_valkey())),
          latest_fn=latest_sample_time_by_host,
          scan_fn=scan_host_pairs,
          interval_s=interval_s,
          window_minutes=cfg.get_ingest_coverage_window_minutes(),
          timeout_ms=cfg.get_ingest_coverage_query_timeout_ms(),
          log_fn=lambda msg: log_print(msg, flush=True),
        )
      except Exception as exc:  # one bad sweep must not kill the daemon
        log_print(f"ingest_coverage sweep failed: {exc!r}", flush=True)
      finally:
        close_old_connections()
    _stop.wait(max(1.0, interval_s - (time.monotonic() - started)))
  return 0


if __name__ == "__main__":
  sys.exit(main())
