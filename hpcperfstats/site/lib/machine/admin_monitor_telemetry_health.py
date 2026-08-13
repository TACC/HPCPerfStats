"""
Staff Admin Monitor telemetry-health scan over recent host_data.

Reports (type, event) pairs that are all-zero over a bounded window and
missing expected core monitor types, scoped to a Redis ``recent_host``
sample (not a site-wide hypertable GROUP BY). Excludes names containing
``error`` (case-insensitive). Soft-fails on PostgreSQL statement timeout
or an empty Redis inventory.

Attributes:
  ALL_ZERO_RESULT_LIMIT: Max all-zero (type, event) rows returned.
  EXPECTED_CORE_TYPES: Small always-on CPU-node type list for missing checks.
  HOST_BATCH_SIZE: Hosts per ``host = ANY`` SQL batch.
  HOST_SAMPLE_LIMIT: Max Redis FQDNs included in the aggregate scan.
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
from hpcperfstats.site.lib.machine.host_data_latest import (
    list_recent_host_fqdns_from_redis,
)

logger = logging.getLogger(__name__)

# Lookback for post-deploy "is the monitor emitting numbers?" checks.
WINDOW_HOURS = 12

# Bound each batch; host-scoped scans should finish well under this.
STATEMENT_TIMEOUT_MS = 45_000

# Caps response size only (does not reduce aggregate scan cost).
ALL_ZERO_RESULT_LIMIT = 500

# Cap Redis inventory sample (hpcperfstats04 EXPLAIN: 8 hosts ~1.88e6 cost).
HOST_SAMPLE_LIMIT = 16

# Hosts per ``host = ANY`` batch (two batches max at HOST_SAMPLE_LIMIT).
HOST_BATCH_SIZE = 8

# Deliberately small: types expected on every CPU node in this fleet.
EXPECTED_CORE_TYPES: tuple[str, ...] = (
    HOST_CPU_TYPE,
    HOST_MEM_TYPE,
    HOST_BLOCK_TYPE,
    HOST_NET_TYPE,
    HOST_NUMA_TYPE,
)


class EmptyRecentHostInventory(Exception):
    """Raised when Redis has no ``recent_host:*`` FQDNs to sample."""


def build_telemetry_health_payload(
    type_event_rows: Sequence[Mapping[str, Any]],
    *,
    computed_at: datetime | None = None,
    timed_out: bool = False,
    error: str | None = None,
    expected_core_types: Sequence[str] | None = None,
    all_zero_limit: int | None = None,
    window_hours: int | None = None,
    hosts_sampled: int | None = None,
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
      hosts_sampled (int | None): Count of Redis FQDNs included in the scan;
        included in ``ok_summary`` when not None.

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
      ...     hosts_sampled=8,
      ... )
      >>> payload["all_zero_events"][0]["type"]
      'host_cpu'
      >>> payload["ok_summary"]["hosts_sampled"]
      8
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

    def _ok_summary(
        *,
        nonzero_pairs: int,
        scanned_note: str,
    ) -> dict[str, Any]:
        """
        Build the ``ok_summary`` mapping, optionally including sample size.

        Args:
          nonzero_pairs (int): Count of (type, event) pairs with a non-zero
            sample.
          scanned_note (str): Human-readable scan summary for the panel.

        Returns:
          dict[str, Any]: ``ok_summary`` wire fragment.

        Examples:
          >>> _ok_summary(nonzero_pairs=1, scanned_note="ok")["nonzero_type_event_pairs"]
          1
        """
        summary: dict[str, Any] = {
            "nonzero_type_event_pairs": nonzero_pairs,
            "scanned_note": scanned_note,
        }
        if hosts_sampled is not None:
            summary["hosts_sampled"] = int(hosts_sampled)
        return summary

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
            "ok_summary": _ok_summary(
                nonzero_pairs=0,
                scanned_note=(
                    f"Scan timed out within {hours}h window; "
                    "expand again or refresh after load eases."
                ),
            ),
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
    sample_suffix = ""
    if hosts_sampled is not None:
        sample_suffix = (
            f" Sampled {int(hosts_sampled)} recently reporting host(s)."
        )
    scanned_note = (
        f"Scanned non-error (type, event) pairs in the last {hours} hours; "
        f"{nonzero_pairs} pair(s) had at least one non-zero value or arc."
        f"{sample_suffix}"
    )
    if healthy:
        scanned_note = (
            f"No all-zero or missing-core anomalies in the last {hours} hours "
            f"({nonzero_pairs} non-zero type/event pair(s))."
            f"{sample_suffix}"
        )

    return {
        "window_hours": hours,
        "computed_at": when.isoformat(),
        "timed_out": False,
        "error": error,
        "all_zero_events": all_zero,
        "missing_core_types": missing,
        "truncated": truncated,
        "ok_summary": _ok_summary(
            nonzero_pairs=nonzero_pairs,
            scanned_note=scanned_note,
        ),
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


def _merge_type_event_batches(
    batches: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """
    Merge per-batch (type, event) aggregates by summing counts and OR-ing nonzero.

    Args:
      batches (Sequence[Sequence[Mapping[str, Any]]]): Rows from each
        ``host = ANY`` batch query.

    Returns:
      list[dict[str, Any]]: Merged rows sorted by ``(type, event)``.

    Examples:
      >>> _merge_type_event_batches(
      ...     [[{"type": "a", "event": "e", "row_count": 1, "has_nonzero": False}],
      ...      [{"type": "a", "event": "e", "row_count": 2, "has_nonzero": True}]]
      ... )
      [{'type': 'a', 'event': 'e', 'row_count': 3, 'has_nonzero': True}]
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for batch in batches:
        for row in batch:
            type_name = str(row.get("type") or "")
            event_name = str(row.get("event") or "")
            key = (type_name, event_name)
            existing = merged.get(key)
            row_count = int(row.get("row_count") or 0)
            has_nonzero = bool(row.get("has_nonzero"))
            if existing is None:
                merged[key] = {
                    "type": type_name,
                    "event": event_name,
                    "row_count": row_count,
                    "has_nonzero": has_nonzero,
                }
            else:
                existing["row_count"] = int(existing["row_count"]) + row_count
                existing["has_nonzero"] = bool(existing["has_nonzero"]) or has_nonzero
    return [merged[k] for k in sorted(merged.keys())]


def _fetch_type_event_aggregates(
    *,
    window_hours: int,
    host_sample_limit: int | None = None,
    host_batch_size: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Run Redis-scoped ``host_data`` GROUP BY batches under statement_timeout.

    Reads FQDNs from Redis ``recent_host:*``, caps the sample, and queries
    ``WHERE host = ANY(%s::text[])`` in batches so Timescale can use
    host+time indexes. Does **not** fall back to a site-wide GROUP BY.

    Args:
      window_hours (int): Lookback hours for ``time >= now() - interval``.
      host_sample_limit (int | None): Max FQDNs to sample; defaults to
        ``HOST_SAMPLE_LIMIT``.
      host_batch_size (int | None): Hosts per SQL batch; defaults to
        ``HOST_BATCH_SIZE``.

    Returns:
      tuple[list[dict[str, Any]], int]: Merged rows with ``type``, ``event``,
      ``row_count``, ``has_nonzero``, plus the number of hosts sampled.

    Raises:
      EmptyRecentHostInventory: When Redis has no FQDN inventory.
      Exception: Propagates DB errors (including statement timeout) to the
        caller for soft-fail handling.

    Examples:
      >>> _fetch_type_event_aggregates(window_hours=12)  # doctest: +SKIP
      ([{'type': 'host_cpu', 'event': 'user', 'row_count': 1, 'has_nonzero': True}], 8)
    """
    hours = max(1, int(window_hours))
    sample_limit = max(
        1,
        int(host_sample_limit if host_sample_limit is not None else HOST_SAMPLE_LIMIT),
    )
    batch_size = max(
        1,
        int(host_batch_size if host_batch_size is not None else HOST_BATCH_SIZE),
    )
    fqdns = sorted(set(list_recent_host_fqdns_from_redis()))
    if not fqdns:
        raise EmptyRecentHostInventory(
            "No recent_host FQDN inventory in Redis; "
            "telemetry health scan skipped (not a healthy signal)."
        )
    sampled = fqdns[:sample_limit]

    sql = """
        SELECT type,
               event,
               COUNT(*)::bigint AS row_count,
               BOOL_OR(
                 COALESCE(value, 0) <> 0 OR COALESCE(arc, 0) <> 0
               ) AS has_nonzero
        FROM host_data
        WHERE host = ANY(%s::text[])
          AND time >= now() - (%s * INTERVAL '1 hour')
          AND type NOT ILIKE '%%error%%'
          AND event NOT ILIKE '%%error%%'
        GROUP BY type, event
        ORDER BY type, event
    """
    batches: list[list[dict[str, Any]]] = []
    using = getattr(connection, "alias", None) or "default"
    with transaction.atomic(using=using):
        with connection.cursor() as cursor:
            cursor.execute(
                "SET LOCAL statement_timeout = %s",
                [STATEMENT_TIMEOUT_MS],
            )
            cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            for i in range(0, len(sampled), batch_size):
                chunk = sampled[i : i + batch_size]
                cursor.execute(sql, [chunk, hours])
                batch_rows: list[dict[str, Any]] = []
                for type_name, event_name, row_count, has_nonzero in cursor.fetchall():
                    batch_rows.append(
                        {
                            "type": type_name,
                            "event": event_name,
                            "row_count": int(row_count or 0),
                            "has_nonzero": bool(has_nonzero),
                        }
                    )
                batches.append(batch_rows)
    return _merge_type_event_batches(batches), len(sampled)


def compute_telemetry_health(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    Compute (and optionally cache) Admin Monitor telemetry health.

    On PostgreSQL timeout, empty Redis inventory, or other DB failure, returns
    a structured ``timed_out`` / error payload instead of raising (HTTP 200
    soft-fail). Never invents missing-core anomalies from a failed scan.

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
                hosts_sampled=0,
            )
        else:
            aggregates, hosts_sampled = _fetch_type_event_aggregates(
                window_hours=WINDOW_HOURS,
            )
            payload = build_telemetry_health_payload(
                aggregates,
                computed_at=computed_at,
                hosts_sampled=hosts_sampled,
            )
    except EmptyRecentHostInventory as exc:
        logger.warning("admin_monitor telemetry_health skipped: %s", exc)
        payload = build_telemetry_health_payload(
            [],
            computed_at=computed_at,
            timed_out=True,
            error=(
                "No recent_host FQDN inventory in Redis; results are "
                "incomplete (not a healthy signal)."
            ),
            hosts_sampled=0,
        )
    except Exception as exc:
        timed_out = _is_statement_timeout(exc)
        if timed_out:
            logger.warning(
                "admin_monitor telemetry_health query failed: %s",
                exc,
            )
        else:
            logger.warning(
                "admin_monitor telemetry_health query failed: %s",
                exc,
                exc_info=True,
            )
        payload = build_telemetry_health_payload(
            [],
            computed_at=computed_at,
            # Soft-fail: never invent missing-core anomalies from a failed scan.
            timed_out=True,
            error=(
                "Telemetry health query timed out; results are incomplete "
                "(not a healthy signal)."
                if timed_out
                else (
                    "Telemetry health query failed; results are incomplete "
                    "(not a healthy signal)."
                )
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
