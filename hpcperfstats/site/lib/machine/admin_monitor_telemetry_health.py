"""
Staff Admin Monitor telemetry-health scan over recent host_data.

Reports severity-classified findings (actionable core missing/all-zero vs
informational idle zeros), joins Redis ``monitor_identity`` for sampled hosts,
and authors ``monitor_handoff_markdown`` for monitor-focused Cursor handoffs.
Scoped to a Redis ``recent_host`` sample (not a site-wide hypertable GROUP BY).
Excludes names containing ``error`` (case-insensitive). Soft-fails on
PostgreSQL statement timeout or an empty Redis inventory.

Attributes:
  ACTIONABLE_FINDING_KINDS: Finding kinds shown by default in UI/handoff.
  ALL_ZERO_RESULT_LIMIT: Max all-zero (type, event) rows retained overall.
  EXPECTED_CORE_TYPES: Small always-on CPU-node type list for missing checks.
  HOST_BATCH_SIZE: Hosts per ``host = ANY`` SQL batch.
  HOST_SAMPLE_LIMIT: Max Redis FQDNs included in the aggregate scan.
  INFORMATIONAL_ALL_ZERO_CAP: Cap for low-severity all-zero findings retained.
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
from hpcperfstats.lib.monitor_identity import load_monitor_identities_for_hosts
from hpcperfstats.site.lib.machine.cache_utils import (
    KEY_ADMIN_TELEMETRY_HEALTH,
    TIMEOUT_ADMIN_STATS,
    _get_redis_py_client,
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

# Informational (non-core) all-zero findings retained for optional expand.
INFORMATIONAL_ALL_ZERO_CAP = 50

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

ACTIONABLE_FINDING_KINDS: frozenset[str] = frozenset(
    {
        "incomplete_scan",
        "missing_core_type",
        "all_zero_core_event",
    }
)


class EmptyRecentHostInventory(Exception):
    """Raised when Redis has no ``recent_host:*`` FQDNs to sample."""


def _finding(
    *,
    kind: str,
    severity: str,
    message: str,
    type_name: str | None = None,
    event_name: str | None = None,
    row_count: int | None = None,
    fqdn: str | None = None,
) -> dict[str, Any]:
    """
    Build one severity-classified finding for the telemetry_health payload.

    Args:
      kind (str): Stable finding kind (e.g. ``missing_core_type``).
      severity (str): ``high``, ``medium``, or ``low``.
      message (str): Operator-facing one-line explanation.
      type_name (str | None): Optional monitor type name.
      event_name (str | None): Optional event name.
      row_count (int | None): Optional row count for all-zero findings.
      fqdn (str | None): Optional host FQDN for per-host findings.

    Returns:
      dict[str, Any]: Finding mapping (omits None optional fields).

    Examples:
      >>> _finding(kind="missing_core_type", severity="high",
      ...          message="missing", type_name="host_mem")["kind"]
      'missing_core_type'
    """
    out: dict[str, Any] = {
        "kind": kind,
        "severity": severity,
        "message": message,
    }
    if type_name is not None:
        out["type"] = type_name
    if event_name is not None:
        out["event"] = event_name
    if row_count is not None:
        out["row_count"] = int(row_count)
    if fqdn is not None:
        out["fqdn"] = fqdn
    return out


def _format_monitor_handoff_markdown(
    *,
    computed_at_iso: str,
    window_hours: int,
    hosts_sampled_fqdns: Sequence[str],
    monitor_identities: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    timed_out: bool,
    error: str | None,
    truncated: bool,
    informational_truncated: bool,
    informational_count: int,
) -> str:
    """
    Author the monitor-focused Markdown handoff report for Admin Copy.

    Args:
      computed_at_iso (str): ISO timestamp of the scan.
      window_hours (int): Lookback hours.
      hosts_sampled_fqdns (Sequence[str]): Sampled Redis FQDNs.
      monitor_identities (Sequence[Mapping[str, Any]]): Joined identity docs.
      findings (Sequence[Mapping[str, Any]]): Classified findings list.
      timed_out (bool): Incomplete-scan flag.
      error (str | None): Soft-fail error text.
      truncated (bool): Whether actionable all-zeros hit the hard cap.
      informational_truncated (bool): Whether informational zeros were capped.
      informational_count (int): Count of informational all-zero findings.

    Returns:
      str: Markdown report body.

    Examples:
      >>> md = _format_monitor_handoff_markdown(
      ...     computed_at_iso="2024-01-01T00:00:00+00:00",
      ...     window_hours=12,
      ...     hosts_sampled_fqdns=["a.example.com"],
      ...     monitor_identities=[],
      ...     findings=[],
      ...     timed_out=False,
      ...     error=None,
      ...     truncated=False,
      ...     informational_truncated=False,
      ...     informational_count=0,
      ... )
      >>> "Telemetry health handoff" in md
      True
    """
    lines: list[str] = [
        "# Telemetry health handoff (Admin Monitor)",
        "",
        f"- computed_at: `{computed_at_iso}`",
        f"- window_hours: {int(window_hours)}",
        f"- hosts_sampled: {len(hosts_sampled_fqdns)}",
    ]
    if hosts_sampled_fqdns:
        lines.append(
            "- sampled_fqdns: "
            + ", ".join(f"`{h}`" for h in hosts_sampled_fqdns)
        )
    lines.extend(["", "## Monitor signatures", ""])
    if not monitor_identities:
        lines.append(
            "No `monitor_identity:{fqdn}` Redis documents for sampled hosts "
            "(slug pending RPM / listend identity not yet written)."
        )
    else:
        for ident in monitor_identities:
            fqdn = str(ident.get("fqdn") or "?")
            ver = ident.get("package_version") or "unknown"
            slug = ident.get("capability_slug")
            slug_txt = slug if slug else "slug pending RPM"
            uname = ident.get("uname") or "—"
            schema = ident.get("schema_types") or []
            schema_note = (
                f"{len(schema)} type(s) in last `!` schema"
                if schema
                else "no `!` schema types stored"
            )
            lines.append(
                f"- `{fqdn}` — version `{ver}`, build `{slug_txt}`, "
                f"uname `{uname}`, {schema_note}"
            )
    lines.extend(["", "## Interpretation", ""])
    if timed_out:
        lines.append(
            "**Incomplete scan — do not conclude healthy or broken.** "
            + (error or "Results are incomplete.")
        )
    else:
        lines.append(
            "Actionable findings below are core missing types and core "
            "all-zero events only. Idle IB/GPU/PMC zeros are informational "
            "and omitted from the primary tables (may be normal)."
        )
        lines.append("")
        lines.append(
            "When a host identity has `schema_types`: type not in last `!` "
            "schema → not expected from this binary; type in schema but "
            "missing from `host_data` → emit/ingest gap."
        )
    lines.append("")
    lines.append(
        "Monitor `$build {capability_slug}` emit is a separate "
        "monitor-workspace task; consumer already tolerates a missing slug."
    )

    # Primary handoff tables: actionable kinds + signature_absent.
    primary = [
        f
        for f in findings
        if str(f.get("kind") or "")
        in ACTIONABLE_FINDING_KINDS | {"signature_absent", "incomplete_scan"}
    ]

    lines.extend(["", "## Actionable findings", ""])
    if timed_out:
        lines.append(
            "Scan incomplete — no actionable anomaly tables (do not invent "
            "missing-core from a failed query)."
        )
    elif not primary:
        lines.append("None.")
    else:
        lines.append("| Severity | Kind | Detail |")
        lines.append("|----------|------|--------|")
        for f in primary:
            detail = str(f.get("message") or "")
            type_name = f.get("type")
            event_name = f.get("event")
            if type_name and event_name:
                detail = f"`{type_name}` / `{event_name}` — {detail}"
            elif type_name:
                detail = f"`{type_name}` — {detail}"
            fqdn = f.get("fqdn")
            if fqdn:
                detail = f"`{fqdn}` — {detail}"
            lines.append(
                f"| {f.get('severity')} | `{f.get('kind')}` | {detail} |"
            )

    if truncated:
        lines.extend(
            [
                "",
                "_Actionable all-zero list truncated at the hard cap._",
            ]
        )
    if informational_count:
        note = (
            f"{informational_count} informational all-zero "
            f"(type, event) pair(s) omitted from primary tables"
        )
        if informational_truncated:
            note += " (list capped)"
        lines.extend(["", f"_{note}._"])

    lines.extend(
        [
            "",
            "## Out of scope / anti-patterns",
            "",
            "- Do **not** run a site-wide `host_data` `GROUP BY type, event`.",
            "- Incomplete scan ≠ healthy; do not treat empty anomaly lists "
            "on timeout as a clean bill of health.",
            "- Do not treat idle IB/GPU/PMC zeros as stuck core telemetry "
            "without schema/identity context.",
            "",
        ]
    )
    return "\n".join(lines)


def build_telemetry_health_payload(
    type_event_rows: Sequence[Mapping[str, Any]],
    *,
    computed_at: datetime | None = None,
    timed_out: bool = False,
    error: str | None = None,
    expected_core_types: Sequence[str] | None = None,
    all_zero_limit: int | None = None,
    informational_all_zero_cap: int | None = None,
    window_hours: int | None = None,
    hosts_sampled: int | None = None,
    hosts_sampled_fqdns: Sequence[str] | None = None,
    monitor_identities: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Build the Admin Monitor ``telemetry_health`` response from aggregate rows.

    Each input row must provide ``type``, ``event``, ``row_count``, and
    ``has_nonzero`` (truthy when any sample had non-zero ``value`` or ``arc``).
    Classifies all-zero pairs into actionable core vs informational other, and
    always authors ``monitor_handoff_markdown``.

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
      all_zero_limit (int | None): Max actionable all-zero pairs returned;
        defaults to ``ALL_ZERO_RESULT_LIMIT``.
      informational_all_zero_cap (int | None): Cap for informational all-zero
        findings; defaults to ``INFORMATIONAL_ALL_ZERO_CAP``.
      window_hours (int | None): Window hours reported in the payload; defaults
        to ``WINDOW_HOURS``.
      hosts_sampled (int | None): Count of Redis FQDNs included in the scan;
        included in ``ok_summary`` when not None.
      hosts_sampled_fqdns (Sequence[str] | None): Sampled FQDN list.
      monitor_identities (Sequence[Mapping[str, Any]] | None): Redis identity
        documents for sampled hosts.

    Returns:
      dict[str, Any]: Wire-ready ``telemetry_health`` mapping including
      ``findings``, ``hosts_sampled_fqdns``, ``monitor_identities``,
      ``monitor_handoff_markdown``, plus legacy ``all_zero_events`` /
      ``missing_core_types`` (actionable-only for quiet defaults).

    Examples:
      >>> payload = build_telemetry_health_payload(
      ...     [{"type": "host_cpu", "event": "user", "row_count": 10,
      ...       "has_nonzero": False},
      ...      {"type": "host_ib", "event": "xmit", "row_count": 3,
      ...       "has_nonzero": False}],
      ...     computed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
      ...     hosts_sampled=8,
      ...     hosts_sampled_fqdns=["a.example.com"],
      ...     expected_core_types=("host_cpu", "host_mem"),
      ... )
      >>> payload["all_zero_events"][0]["type"]
      'host_cpu'
      >>> any(f["kind"] == "all_zero_other_event" for f in payload["findings"])
      True
      >>> "Actionable findings" in payload["monitor_handoff_markdown"]
      True
    """
    cores = tuple(expected_core_types or EXPECTED_CORE_TYPES)
    core_set = set(cores)
    limit = (
        ALL_ZERO_RESULT_LIMIT if all_zero_limit is None else max(0, int(all_zero_limit))
    )
    info_cap = (
        INFORMATIONAL_ALL_ZERO_CAP
        if informational_all_zero_cap is None
        else max(0, int(informational_all_zero_cap))
    )
    hours = WINDOW_HOURS if window_hours is None else int(window_hours)
    when = computed_at or dj_timezone.now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    fqdns = [str(h) for h in (hosts_sampled_fqdns or []) if h]
    identities = [dict(m) for m in (monitor_identities or [])]
    identity_by_fqdn = {
        str(m.get("fqdn") or ""): m for m in identities if m.get("fqdn")
    }

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
        elif fqdns:
            summary["hosts_sampled"] = len(fqdns)
        return summary

    if timed_out:
        findings = [
            _finding(
                kind="incomplete_scan",
                severity="high",
                message=(
                    error
                    or (
                        "Telemetry health query timed out; results are "
                        "incomplete (not a healthy signal)."
                    )
                ),
            )
        ]
        err = error or (
            "Telemetry health query timed out; results are incomplete "
            "(not a healthy signal)."
        )
        md = _format_monitor_handoff_markdown(
            computed_at_iso=when.isoformat(),
            window_hours=hours,
            hosts_sampled_fqdns=fqdns,
            monitor_identities=identities,
            findings=findings,
            timed_out=True,
            error=err,
            truncated=False,
            informational_truncated=False,
            informational_count=0,
        )
        return {
            "window_hours": hours,
            "computed_at": when.isoformat(),
            "timed_out": True,
            "error": err,
            "all_zero_events": [],
            "missing_core_types": [],
            "truncated": False,
            "hosts_sampled_fqdns": fqdns,
            "monitor_identities": identities,
            "findings": findings,
            "monitor_handoff_markdown": md,
            "ok_summary": _ok_summary(
                nonzero_pairs=0,
                scanned_note=(
                    f"Scan timed out within {hours}h window; "
                    "expand again or refresh after load eases."
                ),
            ),
        }

    observed_types: set[str] = set()
    actionable_zeros: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    nonzero_pairs = 0
    truncated = False
    informational_truncated = False
    informational_count = 0

    # Prefer core all-zeros first so the actionable cap is not wasted on IB/GPU.
    zero_rows: list[Mapping[str, Any]] = []
    for row in type_event_rows:
        type_name = str(row.get("type") or "")
        if type_name:
            observed_types.add(type_name)
        if bool(row.get("has_nonzero")):
            nonzero_pairs += 1
            continue
        zero_rows.append(row)

    def _zero_sort_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
        """
        Sort all-zero rows so core types precede informational ones.

        Args:
          row (Mapping[str, Any]): Aggregate row with ``type`` / ``event``.

        Returns:
          tuple[int, str, str]: Sort key (core first, then type, event).

        Examples:
          >>> _zero_sort_key({"type": "host_cpu", "event": "user"})[0]
          0
        """
        t = str(row.get("type") or "")
        return (0 if t in core_set else 1, t, str(row.get("event") or ""))

    for row in sorted(zero_rows, key=_zero_sort_key):
        type_name = str(row.get("type") or "")
        event_name = str(row.get("event") or "")
        row_count = int(row.get("row_count") or 0)
        if type_name in core_set:
            if len(actionable_zeros) < limit:
                actionable_zeros.append(
                    {
                        "type": type_name,
                        "event": event_name,
                        "row_count": row_count,
                    }
                )
                findings.append(
                    _finding(
                        kind="all_zero_core_event",
                        severity="high",
                        message=(
                            "Core type/event is all-zero over the sampled "
                            "window (investigation signal)."
                        ),
                        type_name=type_name,
                        event_name=event_name,
                        row_count=row_count,
                    )
                )
            else:
                truncated = True
        else:
            informational_count += 1
            if informational_count <= info_cap:
                findings.append(
                    _finding(
                        kind="all_zero_other_event",
                        severity="low",
                        message=(
                            "Non-core type/event is all-zero; may be normal "
                            "idle or sparse telemetry."
                        ),
                        type_name=type_name,
                        event_name=event_name,
                        row_count=row_count,
                    )
                )
            else:
                informational_truncated = True

    missing = [t for t in cores if t not in observed_types]
    for type_name in missing:
        findings.append(
            _finding(
                kind="missing_core_type",
                severity="high",
                message=(
                    "Expected core monitor type has zero rows in the "
                    "sampled window."
                ),
                type_name=type_name,
            )
        )

    for fqdn in fqdns:
        if fqdn not in identity_by_fqdn:
            findings.append(
                _finding(
                    kind="signature_absent",
                    severity="medium",
                    message=(
                        "No Redis monitor_identity for this sampled host "
                        "(slug pending / identity not yet written)."
                    ),
                    fqdn=fqdn,
                )
            )

    actionable_findings = [
        f
        for f in findings
        if str(f.get("kind") or "") in ACTIONABLE_FINDING_KINDS
    ]
    healthy = (
        not timed_out
        and not actionable_findings
        and nonzero_pairs > 0
    )
    sample_suffix = ""
    sampled_n = hosts_sampled if hosts_sampled is not None else (
        len(fqdns) if fqdns else None
    )
    if sampled_n is not None:
        sample_suffix = (
            f" Sampled {int(sampled_n)} recently reporting host(s)."
        )
    scanned_note = (
        f"Scanned non-error (type, event) pairs in the last {hours} hours; "
        f"{nonzero_pairs} pair(s) had at least one non-zero value or arc."
        f"{sample_suffix}"
    )
    if healthy:
        scanned_note = (
            f"No actionable all-zero or missing-core anomalies in the last "
            f"{hours} hours ({nonzero_pairs} non-zero type/event pair(s))."
            f"{sample_suffix}"
        )
        if informational_count:
            scanned_note += (
                f" {informational_count} informational all-zero pair(s) "
                "hidden by default."
            )

    md = _format_monitor_handoff_markdown(
        computed_at_iso=when.isoformat(),
        window_hours=hours,
        hosts_sampled_fqdns=fqdns,
        monitor_identities=identities,
        findings=findings,
        timed_out=False,
        error=error,
        truncated=truncated,
        informational_truncated=informational_truncated,
        informational_count=informational_count,
    )

    return {
        "window_hours": hours,
        "computed_at": when.isoformat(),
        "timed_out": False,
        "error": error,
        "all_zero_events": actionable_zeros,
        "missing_core_types": missing,
        "truncated": truncated,
        "hosts_sampled_fqdns": fqdns,
        "monitor_identities": identities,
        "findings": findings,
        "monitor_handoff_markdown": md,
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
) -> tuple[list[dict[str, Any]], list[str]]:
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
      tuple[list[dict[str, Any]], list[str]]: Merged rows with ``type``,
      ``event``, ``row_count``, ``has_nonzero``, plus the sampled FQDN list.

    Raises:
      EmptyRecentHostInventory: When Redis has no FQDN inventory.
      Exception: Propagates DB errors (including statement timeout) to the
        caller for soft-fail handling.

    Examples:
      >>> _fetch_type_event_aggregates(window_hours=12)  # doctest: +SKIP
      ([{'type': 'host_cpu', 'event': 'user', 'row_count': 1, 'has_nonzero': True}],
      ...  ['a.example.com'])
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
    return _merge_type_event_batches(batches), list(sampled)


def compute_telemetry_health(*, force_refresh: bool = False) -> dict[str, Any]:
    """
    Compute (and optionally cache) Admin Monitor telemetry health.

    On PostgreSQL timeout, empty Redis inventory, or other DB failure, returns
    a structured ``timed_out`` / error payload instead of raising (HTTP 200
    soft-fail). Never invents missing-core anomalies from a failed scan.
    Joins Redis ``monitor_identity:{fqdn}`` for sampled hosts when available.

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
                hosts_sampled_fqdns=[],
                monitor_identities=[],
            )
        else:
            aggregates, sampled_fqdns = _fetch_type_event_aggregates(
                window_hours=WINDOW_HOURS,
            )
            identities: list[dict[str, Any]] = []
            try:
                identities = load_monitor_identities_for_hosts(
                    _get_redis_py_client(),
                    sampled_fqdns,
                )
            except Exception:
                identities = []
            payload = build_telemetry_health_payload(
                aggregates,
                computed_at=computed_at,
                hosts_sampled=len(sampled_fqdns),
                hosts_sampled_fqdns=sampled_fqdns,
                monitor_identities=identities,
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
            hosts_sampled_fqdns=[],
            monitor_identities=[],
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
            hosts_sampled_fqdns=[],
            monitor_identities=[],
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
