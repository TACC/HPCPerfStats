"""Cheap host_data freshness helpers (LATERAL per-host max, site-wide LIMIT 1).

Avoid multi-day ``GROUP BY host, max(time)`` over the hypertable — that path
scans hundreds of millions of rows on large sites (Admin Monitor hang).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from django.db import connections, transaction
from django.db.models import Max
from django.utils import timezone

from hpcperfstats.site.lib.machine.models import host_data

# PostgreSQL uses a per-host LATERAL + LIMIT 1 (index probe on (host, time))
# inside a short transaction with parallel workers disabled for the probe.
HOST_LAST_TIME_LOOKUP_BATCH = 64

# Site-wide / fallback window for Admin Monitor freshness (not 8 days).
HOST_DATA_FRESHNESS_WINDOW = timedelta(hours=3)


def latest_sample_time_by_host(hosts, *, batch_size=None):
    """Map host -> max(host_data.time) for ``hosts``, using bounded batches.

    On PostgreSQL: ``LEFT JOIN LATERAL (... ORDER BY time DESC LIMIT 1)`` so
    each host is an index-backed probe. Non-PostgreSQL: ``Max(time)`` with
    ``host__in`` batches.
    """
    latest_by_host = {}
    if not hosts:
        return latest_by_host
    host_list = sorted(hosts)
    batch = max(
        1,
        int(batch_size if batch_size is not None else HOST_LAST_TIME_LOOKUP_BATCH),
    )
    conn = connections["default"]
    if conn.vendor == "postgresql":
        ops = conn.ops
        tbl = ops.quote_name(host_data._meta.db_table)
        col_host = ops.quote_name("host")
        col_time = ops.quote_name("time")
        # DISTINCT ON + ORDER BY over many hypertable chunks can still trigger
        # huge parallel sorts. One backward index scan per host stays bounded.
        sql = (
            "SELECT h.host_val, m.{t} FROM unnest(%s::text[]) AS h(host_val) "
            "LEFT JOIN LATERAL ("
            " SELECT d.{t} FROM {tbl} d WHERE d.{h} = h.host_val "
            " ORDER BY d.{t} DESC LIMIT 1"
            ") AS m ON TRUE"
        ).format(h=col_host, t=col_time, tbl=tbl)
        using = getattr(conn, "alias", None) or "default"
        for i in range(0, len(host_list), batch):
            chunk = host_list[i : i + batch]
            with transaction.atomic(using=using):
                with conn.cursor() as cursor:
                    cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
                    cursor.execute(sql, [chunk])
                    for row_host, row_time in cursor.fetchall():
                        if row_time is not None:
                            latest_by_host[row_host] = row_time
        return latest_by_host

    for i in range(0, len(host_list), batch):
        chunk = host_list[i : i + batch]
        qs = (
            host_data.objects.filter(host__in=chunk)
            .values("host")
            .annotate(last_time=Max("time"))
        )
        for row in qs:
            latest_by_host[row.get("host")] = row.get("last_time")
    return latest_by_host


def latest_sample_time_by_host_in_window(window=None):
    """Map host -> max(time) for hosts with samples in ``window`` (default 3h).

    Last-resort Admin Monitor path when Redis ``recent_host`` inventory is empty.
    Never use multi-day (e.g. 8d) windows here.
    """
    if window is None:
        window = HOST_DATA_FRESHNESS_WINDOW
    now = timezone.now()
    time_bounds = now - window
    latest_by_host = {}
    qs = (
        host_data.objects.filter(time__gte=time_bounds)
        .values("host")
        .annotate(last_time=Max("time"))
    )
    for row in qs:
        host = row.get("host")
        last_time = row.get("last_time")
        if host and last_time is not None:
            latest_by_host[host] = last_time
    return latest_by_host


def newest_host_data_sample_time(window=None):
    """Return the newest ``host_data.time`` in ``window``, or ``None``.

    Uses ``ORDER BY time DESC LIMIT 1`` (chunk-friendly) — not ``max(time)``
    over an unbounded table.
    """
    if window is None:
        window = HOST_DATA_FRESHNESS_WINDOW
    now = timezone.now()
    time_bounds = now - window
    return (
        host_data.objects.filter(time__gt=time_bounds)
        .order_by("-time")
        .values_list("time", flat=True)
        .first()
    )


def format_host_data_newest_iso(value):
    """Serialize a datetime for Admin Monitor Timescale stats, or ``None``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
