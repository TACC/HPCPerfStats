"""
Staff Admin Monitor telemetry-health scan over recent host_data.

Reports site-wide (type, event) pairs that are all-zero over a bounded
window and missing expected core monitor types. Excludes names containing
``error`` (case-insensitive). Soft-fails on PostgreSQL statement timeout.

Attributes:
  ALL_ZERO_RESULT_LIMIT: Max all-zero (type, event) rows returned.
  EXPECTED_CORE_TYPES: Small always-on CPU-node type list for missing checks.
  STATEMENT_TIMEOUT_MS: Per-query PostgreSQL statement_timeout (milliseconds).
  WINDOW_HOURS: Lookback window for the aggregate scan.
  logger: Module logger for soft-fail warnings.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import logging
from datetime import datetime, timezone

from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone as dj_timezone

from hpcperfstats.dbload.lib.monitor_naming.canonical import (
    HOST_BLOCK_TYPE,
    HOST_CPU_TYPE,
    HOST_MEM_TYPE,
    HOST_NET_TYPE,
    HOST_NUMA_TYPE,
)
from hpcperfstats.site.lib.machine.cache_utils import (
    KEY_ADMIN_TELEMETRY_HEALTH,
    TIMEOUT_ADMIN_STATS,
)

logger = logging.getLogger(__name__)

# Site-wide lookback for post-deploy "is the monitor emitting numbers?" checks.
WINDOW_HOURS = 12

# Bound each attempt; large sites may still time out — return timed_out soft-fail.
STATEMENT_TIMEOUT_MS = 90_000

# Caps response size only (does not reduce aggregate scan cost).
ALL_ZERO_RESULT_LIMIT = 500

# Deliberately small: types expected on every CPU node in this fleet.
EXPECTED_CORE_TYPES: tuple[str, ...] = (
    HOST_CPU_TYPE,
    HOST_MEM_TYPE,
    HOST_BLOCK_TYPE,
    HOST_NET_TYPE,
    HOST_NUMA_TYPE,
)


def build_telemetry_health_payload(
    type_event_rows: Sequence[Mapping[str, Any]],
    *,
    computed_at: datetime | None = None,
    timed_out: bool = False,
    error: str | None = None,
    expected_core_types: Sequence[str] | None = None,
    all_zero_limit: int | None = None,
    window_hours: int | None = None,
) -> dict[str, Any]:
    """
    Build the Admin Monitor ``telemetry_health`` response from aggregate rows.

    Each input row must provide ``type``, ``event``, ``row_count``, and
    ``has_nonzero`` (truthy when any sample had non-zero ``value`` or ``arc``).

    Args:
      type_event_rows (Sequence[Mapping[str, Any]]): Aggregated (type, event)
        rows from the bounded ``host_data`` scan.
      computed_at (datetime | None): ISO timestamp source; defaults to now
        (UTC-aware).
      timed_out (bool): When True, anomaly lists are empty and
        ``timed_out`` is set so the UI cannot show a false healthy signal.
      error (str | None): Operator-facing error/timeout message, or None.
      expected_core_types (Sequence[str] | None): Core types to require; defaults
        to ``EXPECTED_CORE_TYPES``.
      all_zero_limit (int | None): Max all-zero pairs returned; defaults to
        ``ALL_ZERO_RESULT_LIMIT``.
      window_hours (int | None): Window hours reported in the payload; defaults
        to ``WINDOW_HOURS``.

    Returns:
      dict[str, Any]: Wire-ready ``telemetry_health`` mapping with
      ``window_hours``, ``computed_at``, ``timed_out``, ``error``,
      ``all_zero_events``, ``missing_core_types``, ``truncated``, and
      ``ok_summary``.

    Examples:
      >>> payload = build_telemetry_health_payload(
      ...     [{"type": "host_cpu", "event": "user", "row_count": 10,
      ...       "has_nonzero": False}],
      ...     computed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
      ... )
      >>> payload["all_zero_events"][0]["type"]
      'host_cpu'
      >>> payload["missing_core_types"]
      ['host_mem', 'host_block', 'host_net', 'host_numa']
    """
    cores = tuple(expected_core_types or EXPECTED_CORE_TYPES)
    limit = (
        ALL_ZERO_RESULT_LIMIT if all_zero_limit is None else max(0, int(all_zero_limit))
    )
    hours = WINDOW_HOURS if window_hours is None else int(window_hours)
    when = computed_at or dj_timezone.now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    if timed_out:
        return {
            "window_hours": hours,
            "computed_at": when.isoformat(),
            "timed_out": True,
            "error": error
            or (
                "Telemetry health query timed out; results are incomplete "
                "(not a healthy signal)."
            ),
            "all_zero_events": [],
            "missing_core_types": [],
            "truncated": False,
            "ok_summary": {
                "nonzero_type_event_pairs": 0,
                "scanned_note": (
                    f"Scan timed out within {hours}h window; "
                    "expand again or refresh after load eases."
                ),
            },
        }

    observed_types: set[str] = set()
    all_zero: list[dict[str, Any]] = []
    nonzero_pairs = 0
    truncated = False

    for row in type_event_rows:
        type_name = str(row.get("type") or "")
        event_name = str(row.get("event") or "")
        if type_name:
            observed_types.add(type_name)
        has_nonzero = bool(row.get("has_nonzero"))
        if has_nonzero:
            nonzero_pairs += 1
            continue
        row_count = int(row.get("row_count") or 0)
        if len(all_zero) < limit:
            all_zero.append(
                {
                    "type": type_name,
                    "event": event_name,
                    "row_count": row_count,
                }
            )
        else:
            truncated = True

    missing = [t for t in cores if t not in observed_types]
    healthy = (
        not timed_out
        and not all_zero
        and not missing
        and nonzero_pairs > 0
    )
    scanned_note = (
        f"Scanned non-error (type, event) pairs in the last {hours} hours; "
        f"{nonzero_pairs} pair(s) had at least one non-zero value or arc."
    )
    if healthy:
        scanned_note = (
            f"No all-zero or missing-core anomalies in the last {hours} hours "
            f"({nonzero_pairs} non-zero type/event pair(s))."
        )

    return {
        "window_hours": hours,
        "computed_at": when.isoformat(),
        "timed_out": False,
        "error": error,
        "all_zero_events": all_zero,
        "missing_core_types": missing,
        "truncated": truncated,
        "ok_summary": {
            "nonzero_type_event_pairs": nonzero_pairs,
            "scanned_note": scanned_note,
        },
    }


def _is_statement_timeout(exc: BaseException) -> bool:
    """
    Return True when ``exc`` looks like a PostgreSQL statement timeout.

    Args:
      exc (BaseException): Exception raised by the DB driver or Django.

    Returns:
      bool: True when the message mentions canceling due to statement timeout.

    Examples:
      >>> class _E(Exception):
      ...     pass
      >>> _is_statement_timeout(_E("canceling statement due to statement timeout"))
      True
      >>> _is_statement_timeout(_E("relation does not exist"))
      False
    """
    text = str(exc).lower()
    return "statement timeout" in text or "canceling statement" in text


def _fetch_type_event_aggregates(*, window_hours: int) -> list[dict[str, Any]]:
    """
    Run the bounded ``host_data`` GROUP BY under statement_timeout.

    Args:
      window_hours (int): Lookback hours for ``time >= now() - interval``.

    Returns:
      list[dict[str, Any]]: Rows with ``type``, ``event``, ``row_count``,
      ``has_nonzero``.

    Raises:
      Exception: Propagates DB errors (including statement timeout) to the
        caller for soft-fail handling.

    Examples:
      >>> _fetch_type_event_aggregates(window_hours=12)  # doctest: +SKIP
      [{'type': 'host_cpu', 'event': 'user', 'row_count': 1, 'has_nonzero': True}]
    """
    hours = max(1, int(window_hours))
    sql = """
        SELECT type,
               event,
               COUNT(*)::bigint AS row_count,
               BOOL_OR(
                 COALESCE(value, 0) <> 0 OR COALESCE(arc, 0) <> 0
               ) AS has_nonzero
        FROM host_data
        WHERE time >= now() - (%s * INTERVAL '1 hour')
          AND type NOT ILIKE '%%error%%'
          AND event NOT ILIKE '%%error%%'
        GROUP BY type, event
        ORDER BY type, event
    """
    rows: list[dict[str, Any]] = []
    using = getattr(connection, "alias", None) or "default"
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute(
                "SET LOCAL statement_timeout = %s",
                [STATEMENT_TIMEOUT_MS],
            )
            cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            cursor.execute(sql, [hours])
            for type_name, event_name, row_count, has_nonzero in cursor.fetchall():
                rows.append(
                    {
                        "type": type_name,
                        "event": event_name,
                        "row_count": int(row_count or 0),
                        "has_nonzero": bool(has_nonzero),
                    }
                )
    return rows


def compute_telemetry_health(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    Compute (and optionally cache) Admin Monitor telemetry health.

    On PostgreSQL timeout or other DB failure, returns a structured
    ``timed_out`` / error payload instead of raising (HTTP 200 soft-fail).

    Args:
      force_refresh (bool): When True, skip reading the Django cache entry
        (caller should have deleted the key on ``refresh=1``).

    Returns:
      dict[str, Any]: ``telemetry_health`` payload for the API Response.

    Examples:
      >>> compute_telemetry_health(force_refresh=True)  # doctest: +SKIP
      {'window_hours': 12, 'timed_out': False, ...}
    """
    if not force_refresh:
        try:
            cached = cache.get(KEY_ADMIN_TELEMETRY_HEALTH)
            if isinstance(cached, dict):
                return cached
        except Exception:
            pass

    computed_at = dj_timezone.now()
    try:
        if connection.vendor != "postgresql":
            # Unit tests / SQLite: empty scan → missing cores, not healthy.
            payload = build_telemetry_health_payload(
                [],
                computed_at=computed_at,
            )
        else:
            aggregates = _fetch_type_event_aggregates(window_hours=WINDOW_HOURS)
            payload = build_telemetry_health_payload(
                aggregates,
                computed_at=computed_at,
            )
    except Exception as exc:
        logger.warning(
            "admin_monitor telemetry_health query failed: %s",
            exc,
            exc_info=True,
        )
        timed_out = _is_statement_timeout(exc)
        payload = build_telemetry_health_payload(
            [],
            computed_at=computed_at,
            # Soft-fail: never invent missing-core anomalies from a failed scan.
            timed_out=True,
            error=(
                "Telemetry health query timed out; results are incomplete "
                "(not a healthy signal)."
                if timed_out
                else f"Telemetry health query failed: {exc}"
            ),
        )

    try:
        cache.set(KEY_ADMIN_TELEMETRY_HEALTH, payload, timeout=TIMEOUT_ADMIN_STATS)
    except Exception:
        pass
    return payload


def expected_core_types() -> tuple[str, ...]:
    """
    Return the expected core monitor type names for missing-type checks.

    Returns:
      tuple[str, ...]: Copy of ``EXPECTED_CORE_TYPES``.

    Examples:
      >>> "host_cpu" in expected_core_types()
      True
    """
    return EXPECTED_CORE_TYPES
