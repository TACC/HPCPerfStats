"""
Estimate peak Django PostgreSQL slots versus compose ``max_connections``.

Attributes:
  COMPOSE_PG_MAX_CONNECTIONS: Compose ``db`` GUC; do not raise it to hide leaks.
  RESERVE_SLOTS: Superuser, maintenance, and leftover-process headroom.
  WARN_FRACTION: Log WARN when estimated peak reaches this fraction of the GUC.
"""

from __future__ import annotations

from typing import Any, Callable

from hpcperfstats.dbload.lib.conf_parser import (
    get_api_small_executor_max_workers,
    get_gunicorn_workers,
    get_listend_db_ingest_pool_processes,
    get_metrics_pool_processes,
    get_parallel_db_prefetch_max,
    get_sync_archive_pool_processes,
    get_sync_day_close_max_inflight,
    get_sync_ingest_pool_processes,
)

COMPOSE_PG_MAX_CONNECTIONS = 500
RESERVE_SLOTS = 32
WARN_FRACTION = 0.80


def estimate_django_pg_slot_peak() -> dict[str, Any]:
  """
  Sum gunicorn×executor + listend + metrics + ingest + day_close + reserve.

  This is a **peak occupancy estimate**, not a live ``pg_stat_activity``
  census. A leak (detached metrics threads that skip ``connections.close_all``)
  can exceed this number even when the formula stays under 500.

  Returns:
    dict[str, Any]: ``peak``, ``max_connections``, ``warn_threshold``,
    ``should_warn``, and per-component counts.

  Examples:
    >>> est = estimate_django_pg_slot_peak()
    >>> est["max_connections"] == 500 and "metrics_pool" in est["components"]
    True
  """
  gunicorn = max(1, int(get_gunicorn_workers()))
  executor = max(
      1,
      int(get_api_small_executor_max_workers()),
      int(get_parallel_db_prefetch_max()),
  )
  listend = max(0, int(get_listend_db_ingest_pool_processes()))
  metrics_pool = max(1, int(get_metrics_pool_processes()))
  ingest = max(1, int(get_sync_ingest_pool_processes()))
  day_close = max(0, int(get_sync_day_close_max_inflight()))
  archive = max(0, int(get_sync_archive_pool_processes()))
  web = gunicorn * executor
  peak = (
      web
      + listend
      + metrics_pool
      + ingest
      + day_close
      + archive
      + RESERVE_SLOTS
  )
  warn_threshold = int(COMPOSE_PG_MAX_CONNECTIONS * WARN_FRACTION)
  return {
      "peak": peak,
      "max_connections": COMPOSE_PG_MAX_CONNECTIONS,
      "warn_threshold": warn_threshold,
      "should_warn": peak >= warn_threshold,
      "components": {
          "gunicorn_workers": gunicorn,
          "executor": executor,
          "web": web,
          "listend": listend,
          "metrics_pool": metrics_pool,
          "ingest": ingest,
          "day_close": day_close,
          "archive": archive,
          "reserve": RESERVE_SLOTS,
      },
  }


def log_pg_slot_budget_if_needed(
  log_fn: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
  """
  Log a WARN when the estimated peak reaches 80 percent of 500.

  Log-only: this helper never raises and never changes ``max_connections``.

  Args:
    log_fn (Callable[[str], Any] | None): Logger; defaults to ``print``.

  Returns:
    dict[str, Any]: The estimator payload from
    ``estimate_django_pg_slot_peak``.

  Examples:
    >>> logged = []
    >>> result = log_pg_slot_budget_if_needed(log_fn=logged.append)
    >>> "peak" in result
    True
  """
  emit = print if log_fn is None else log_fn
  try:
    estimate = estimate_django_pg_slot_peak()
  except Exception as exc:
    emit("WARN: pg slot budget estimate failed: %s" % exc)
    return {
        "peak": 0,
        "max_connections": COMPOSE_PG_MAX_CONNECTIONS,
        "warn_threshold": int(COMPOSE_PG_MAX_CONNECTIONS * WARN_FRACTION),
        "should_warn": False,
        "components": {},
    }
  if estimate["should_warn"]:
    emit(
        "WARN: estimated Django PG slot peak %s >= 80%% of max_connections=%s "
        "(warn_threshold=%s components=%s); do not raise the GUC — close leaks"
        % (
            estimate["peak"],
            estimate["max_connections"],
            estimate["warn_threshold"],
            estimate["components"],
        )
    )
  return estimate
