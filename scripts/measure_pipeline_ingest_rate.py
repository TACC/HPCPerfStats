#!/usr/bin/env python3
"""
Measure listend vs sync_timedb rates from pipeline logs.

Reads pipeline container logs (stdin, --log-file, or --fetch-compose) and prints
only summary outcome lines to stdout. Errors and caveats go to stderr.

Backlog fields (backlog_at_start / backlog_latest / drained / empirical ETA) use
**on-disk pending census only**:
- ``pending rescan done pending=N`` (uncapped before queue cap; optional
  historical ``sync_timedb:`` / ``ingest:`` body prefixes)
- ``Pending stats file list truncated pending=N max=M`` (uncapped N at truncate)

Do **not** mix those with ``Throughput telemetry … backlog=N``, which is capped
in-memory ``len(pending_stats_files)`` (≤ sync_ingest_queue_max_size, often
2000). Queue occupancy is reported separately as ingest_queue_depth_*.

The measurement window starts at **ingest start** by default (last ``startup
ingest gate cleared; ingest may begin``), not supervisor/startup maintenance.
Use ``--include-startup`` to measure from the first timestamped line. Fallback
when no gate line is present: last ``chunk imap start``.

``estimated_finish_local`` is log_end (or now) plus the first usable ETA among
empirical / full_ingest / archive_done, formatted in the host local timezone
(``YYYY-MM-DD HH:MM:SS ±HHMM``). ``estimated_finish_basis`` names which ETA was
used.

Usage (from HPCPerfStats/)::

  docker compose logs --timestamps pipeline \\
    2>&1 | python3 scripts/measure_pipeline_ingest_rate.py
  python3 scripts/measure_pipeline_ingest_rate.py \\
    --log-file /tmp/pipeline-full.log

Attributes:
  EVEN_RATIO_TOLERANCE: Attribute.
  LISTEND_REPORT_WINDOW_MINUTES: Attribute.
  _ANSI_ESCAPE_RE: Attribute.
  _ARCHIVE_FINALIZE_RE: Attribute.
  _BOOT_MARKERS: Attribute.
  _CHUNK_IMMEDIATE_RE: Attribute.
  _FULL_INGEST_RE: Attribute.
  _GIB_BYTES: Attribute.
  _INGEST_ELAPSED_S_RE: Attribute.
  _INGEST_POSTGRES_S_RE: Attribute.
  _INGEST_SIZE_BYTES_RE: Attribute.
  _INGEST_START_FALLBACK_MARKERS: Attribute.
  _LISTEND_UNLINKS_RE: Attribute.
  _LOG_TS_CONTAINER_FIRST_RE: Attribute.
  _LOG_TS_CONTAINER_PIPE_RE: Attribute.
  _LOG_TS_LEADING_RE: Attribute.
  _LOG_TS_PIPE_RE: Attribute.
  _MIB_BYTES: Attribute.
  _MID_TIER_NAME: Mid size-tier name for overnight decision pack.
  _PARSE_HOLD_TOKEN_NAMES: Parse-stage token names scraped from ingest lines.
  _PHASE_TOKEN_RES: Compiled named-field regexes for write/parse tokens.
  _PENDING_RESCAN_RE: Attribute.
  _PENDING_TRUNCATE_RE: Attribute.
  _QUEUE_SATURATION_DISK_FACTOR: Attribute.
  _RFC3339_TS: Attribute.
  _SIZE_TIER_BOUNDS: Attribute.
  _SIZE_TIER_NAMES: Attribute.
  _THROUGHPUT_BACKLOG_RE: Attribute.
  _WRITE_PHASE_TOKEN_NAMES: Write-phase token names scraped from ingest lines.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable, Iterator, Optional

# Docker / podman compose log prefixes (RFC3339, optional nanoseconds).
_RFC3339_TS = (
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
# docker compose logs --timestamps: 2026-…T…Z container | message
_LOG_TS_PIPE_RE = re.compile(
    rf"^(?P<ts>{_RFC3339_TS})\s+\S+\s+\|\s+(?P<body>.*)$"
)
# docker compose logs --timestamps: container | 2026-…T… message
_LOG_TS_CONTAINER_PIPE_RE = re.compile(
    rf"^\S+\s+\|\s+(?P<ts>{_RFC3339_TS})\s+(?P<body>.*)$"
)
# docker compose logs --timestamps --names: container 2026-…T… message
_LOG_TS_CONTAINER_FIRST_RE = re.compile(
    rf"^\S+\s+(?P<ts>{_RFC3339_TS})\s+(?P<body>.*)$"
)
# Bare RFC3339 prefix (supervisord / some compose drivers): 2026-…T… message
_LOG_TS_LEADING_RE = re.compile(
    rf"^(?P<ts>{_RFC3339_TS})\s+(?P<body>.*)$"
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_LISTEND_UNLINKS_RE = re.compile(
    r"current file unlinks \(last 10 minutes\): (\d+)"
)
_FULL_INGEST_RE = re.compile(
    r"ingest file path=\S+ .*outcome=ingested\b"
    r"(?=.*\bingest_ok=yes\b)(?=.*\bdb_skip=no\b)"
)
# Named-field extracts (token order on ingest outcome lines is unordered).
_INGEST_SIZE_BYTES_RE = re.compile(r"size_bytes=(?P<size>[0-9]+)")
_INGEST_ELAPSED_S_RE = re.compile(r"elapsed_s=(?P<elapsed>[0-9.]+)")
_INGEST_POSTGRES_S_RE = re.compile(r"postgres_s=(?P<postgres>[0-9.]+)")
_WRITE_PHASE_TOKEN_NAMES = (
    "orm_materialize_s",
    "orm_bulk_prep_s",
    "db_execute_s",
    "db_commit_s",
    "copy_s",
    "conflict_insert_s",
)
_PARSE_HOLD_TOKEN_NAMES = (
    "lock_s",
    "decode_s",
    "feed_s",
    "proc_merge_s",
    "hw_df_s",
    "proc_df_s",
    "delta_s",
    "collapse_s",
    "arc_s",
    "concat_s",
    "start_s",
    "build_df_s",
    "stages_sum_s",
    "parse_unaccounted_s",
)
_PHASE_TOKEN_RES = {
    name: re.compile(r"%s=(?P<v>[0-9.]+)" % re.escape(name))
    for name in (_WRITE_PHASE_TOKEN_NAMES + _PARSE_HOLD_TOKEN_NAMES)
}
_MID_TIER_NAME = "64mib_1gib"
_MIB_BYTES = 1024 * 1024
_GIB_BYTES = 1024 * _MIB_BYTES
_SIZE_TIER_BOUNDS = (
    ("lt_64mib", 0, 64 * _MIB_BYTES),
    ("64mib_1gib", 64 * _MIB_BYTES, _GIB_BYTES),
    ("1_4gib", _GIB_BYTES, 4 * _GIB_BYTES),
    ("ge_4gib", 4 * _GIB_BYTES, None),
)
_SIZE_TIER_NAMES = tuple(name for name, _lo, _hi in _SIZE_TIER_BOUNDS)
_CHUNK_IMMEDIATE_RE = re.compile(
    r"(?:sync_timedb:\s+)?(?:ingest:\s+)?chunk ingest summary .*checkpoint_immediate_n=(\d+)"
)
_ARCHIVE_FINALIZE_RE = re.compile(
    r"(?:sync_timedb:\s+)?(?:ingest:\s+)?checkpoint deferred archive finalize count=(\d+)"
)
_PENDING_RESCAN_RE = re.compile(
    r"(?:sync_timedb:\s+)?(?:ingest:\s+)?pending rescan done pending=(\d+)"
)
_PENDING_TRUNCATE_RE = re.compile(
    r"Pending stats file list truncated pending=(\d+) max=(\d+)"
)
_THROUGHPUT_BACKLOG_RE = re.compile(
    r"Throughput telemetry: active_workers=\d+ backlog=(\d+)"
)
_BOOT_MARKERS = (
    "startup ingest gate cleared; ingest may begin",
)
# Used only when no ingest-gate line is present in the log dump.
_INGEST_START_FALLBACK_MARKERS = (
    "chunk imap start",
)
# Disk pending ≫ queue depth by at least this factor → saturation WARN.
_QUEUE_SATURATION_DISK_FACTOR = 2

EVEN_RATIO_TOLERANCE = 0.02
LISTEND_REPORT_WINDOW_MINUTES = 10.0


@dataclass
class LogMetrics:
    """
    Hold LogMetrics state and behavior.
    
    Attributes:
      archive_finalize_sum: ``archive_finalize_sum``.
      archive_immediate_sum: ``archive_immediate_sum``.
      backlog_disk_samples: ``backlog_disk_samples``.
      backlog_throughput_samples: ``backlog_throughput_samples``.
      first_ts: ``first_ts``.
      full_ingest_count: ``full_ingest_count``.
      full_ingest_bytes: Sum of ``size_bytes`` on full-ingest lines (0 when absent).
      full_ingest_elapsed_by_tier: Per-tier ``elapsed_s`` samples.
      full_ingest_postgres_by_tier: Per-tier ``postgres_s`` samples.
      full_ingest_count_by_tier: Per-tier full-ingest counts.
      full_ingest_phases_by_tier: Per-tier write/parse phase sample lists.
      last_ts: ``last_ts``.
      listend_unlink_samples: Timestamped rolling-window unlink counts.
      listend_unlink_sum: Effective non-overlapping unlink sum for rates.
      timestamped_lines: ``timestamped_lines``.
      truncate_max_samples: ``truncate_max_samples``.
    """
    listend_unlink_samples: list[tuple[Optional[datetime], int]] = field(
        default_factory=list,
    )
    listend_unlink_sum: int = 0
    full_ingest_count: int = 0
    full_ingest_bytes: int = 0
    full_ingest_elapsed_by_tier: dict[str, list[float]] = field(
        default_factory=lambda: {name: [] for name in _SIZE_TIER_NAMES},
    )
    full_ingest_postgres_by_tier: dict[str, list[float]] = field(
        default_factory=lambda: {name: [] for name in _SIZE_TIER_NAMES},
    )
    full_ingest_count_by_tier: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in _SIZE_TIER_NAMES},
    )
    full_ingest_phases_by_tier: dict[str, dict[str, list[float]]] = field(
        default_factory=lambda: {
            name: {tok: [] for tok in (
                _WRITE_PHASE_TOKEN_NAMES + _PARSE_HOLD_TOKEN_NAMES
            )}
            for name in _SIZE_TIER_NAMES
        },
    )
    archive_immediate_sum: int = 0
    archive_finalize_sum: int = 0
    # Uncapped on-disk pending (rescan done + truncate pending=N).
    backlog_disk_samples: list[tuple[datetime, int]] = field(default_factory=list)
    # Observed truncate max= values (queue high watermark hints).
    truncate_max_samples: list[int] = field(default_factory=list)
    # Capped in-memory queue depth from Throughput telemetry backlog=.
    backlog_throughput_samples: list[tuple[datetime, int]] = field(default_factory=list)
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    timestamped_lines: int = 0

    @property
    def backlog_rescan_samples(self) -> list[tuple[datetime, int]]:
        """
        Alias kept for older tests/callers; same as disk samples.
        
        Returns:
          list[tuple[datetime, int]]: list[tuple[datetime, int]] produced by
          this call.
        
        Examples:
          >>> LogMetrics().backlog_rescan_samples()  # doctest: +SKIP
        """
        return self.backlog_disk_samples


def _size_tier_name(size_bytes: int) -> str:
    """
    Map ``size_bytes`` to a cohort label for analyzer summaries.

    Args:
      size_bytes (int): File size from ingest outcome ``size_bytes=``.

    Returns:
      str: Tier name from ``_SIZE_TIER_NAMES``.

    Examples:
      >>> _size_tier_name(0)  # doctest: +SKIP
    """
    for name, lo, hi in _SIZE_TIER_BOUNDS:
        if size_bytes < lo:
            continue
        if hi is None or size_bytes < hi:
            return name
    return _SIZE_TIER_NAMES[-1]


def _optional_float_group(
    match: Optional[re.Match[str]],
    group: str,
) -> Optional[float]:
    """
    Parse a named float group when the regex matched.

    Args:
      match (Optional[re.Match[str]]): Match object, or None.
      group (str): Named group to read.

    Returns:
      Optional[float]: Parsed float, or None when absent/unparseable.

    Examples:
      >>> _optional_float_group(None, "elapsed")  # doctest: +SKIP
    """
    if match is None:
        return None
    raw = match.group(group)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _record_full_ingest(metrics: LogMetrics, body: str) -> None:
    """
    Count a full ingest and accumulate size/elapsed/postgres tier samples.

    Token order on outcome lines is unordered; extracts use named-field regex.

    Args:
      metrics (LogMetrics): Accumulator.
      body (str): Log body after timestamp strip.

    Returns:
      None

    Examples:
      >>> _record_full_ingest(LogMetrics(), "")  # doctest: +SKIP
    """
    metrics.full_ingest_count += 1
    size_match = _INGEST_SIZE_BYTES_RE.search(body)
    elapsed = _optional_float_group(_INGEST_ELAPSED_S_RE.search(body), "elapsed")
    postgres = _optional_float_group(_INGEST_POSTGRES_S_RE.search(body), "postgres")
    size_bytes: Optional[int] = None
    if size_match is not None:
        try:
            size_bytes = int(size_match.group("size"))
        except (TypeError, ValueError):
            size_bytes = None
    if size_bytes is not None and size_bytes >= 0:
        metrics.full_ingest_bytes += size_bytes
        tier = _size_tier_name(size_bytes)
        metrics.full_ingest_count_by_tier[tier] += 1
        if elapsed is not None:
            metrics.full_ingest_elapsed_by_tier[tier].append(elapsed)
        if postgres is not None:
            metrics.full_ingest_postgres_by_tier[tier].append(postgres)
        phase_bucket = metrics.full_ingest_phases_by_tier[tier]
        for tok, cre in _PHASE_TOKEN_RES.items():
            val = _optional_float_group(cre.search(body), "v")
            if val is not None:
                phase_bucket[tok].append(val)


def _median_or_none(samples: list[float]) -> Optional[float]:
    """
    Return the median of samples, or ``None`` when the list is empty.

    Args:
      samples (list[float]): Numeric samples.

    Returns:
      Optional[float]: Median, or ``None`` when empty.

    Examples:
      >>> _median_or_none([]) is None
      True
    """
    if not samples:
        return None
    return float(median(samples))


def _decision_next(
    *,
    ratio_ingest: str,
    window_minutes: float,
    metrics: LogMetrics,
    mid_postgres_frac: Optional[float],
    mid_top_parse_hold: Optional[str],
    mid_write_dominates: bool,
    ge_1gib_wall_share: Optional[float],
    telem_incomplete: bool = False,
    parse_unaccounted_dominates: bool = False,
) -> str:
    """
    Map overnight analyzer signals to the locked next-CODE token.

    Args:
      ratio_ingest (str): ``listend/full_ingest`` ratio string or ``N/A``.
      window_minutes (float): Analyzed window length.
      metrics (LogMetrics): Parsed metrics (for tier counts).
      mid_postgres_frac (Optional[float]): Mid-tier postgres/elapsed median frac.
      mid_top_parse_hold (Optional[str]): Largest mid-tier parse hold name.
      mid_write_dominates (bool): True when execute/copy dominate write phases.
      ge_1gib_wall_share (Optional[float]): Share of elapsed samples in large tiers.
      telem_incomplete (bool): Parse holds present but no write-phase tokens.
      parse_unaccounted_dominates (bool): Mid ``parse_unaccounted_s`` ≥ top named hold.

    Returns:
      str: Decision token for operators (never empty).

    Examples:
      >>> _decision_next(  # doctest: +SKIP
      ...   ratio_ingest="0.5", window_minutes=480.0, metrics=LogMetrics(),
      ...   mid_postgres_frac=0.1, mid_top_parse_hold=None,
      ...   mid_write_dominates=False, ge_1gib_wall_share=0.0,
      ... )
    """
    if window_minutes < 60.0:
        return "short_window_re_soak"
    try:
        ratio = float(ratio_ingest)
    except (TypeError, ValueError):
        return "insufficient_rate_samples"
    mid_n = int(metrics.full_ingest_count_by_tier.get(_MID_TIER_NAME, 0))
    small_n = int(metrics.full_ingest_count_by_tier.get("lt_64mib", 0))
    large_n = int(metrics.full_ingest_count_by_tier.get("1_4gib", 0)) + int(
        metrics.full_ingest_count_by_tier.get("ge_4gib", 0),
    )
    if ratio <= 1.0 + EVEN_RATIO_TOLERANCE:
        return "stop_ingest_rate_watch_archive"
    if ge_1gib_wall_share is not None and ge_1gib_wall_share >= 0.5 and large_n > 0:
        return "giant_scheduling_plan"
    if telem_incomplete:
        return "telem_incomplete_re_soak"
    if mid_write_dominates or (
        mid_postgres_frac is not None and mid_postgres_frac >= 0.35
    ):
        return "write_timescale_path"
    if parse_unaccounted_dominates:
        return "parse_unaccounted_investigate"
    if mid_top_parse_hold:
        return "parse_hold_%s" % mid_top_parse_hold
    if mid_n == 0 and small_n == 0:
        return "insufficient_mid_tier_samples"
    return "parse_or_refill_investigate"


def _fmt_optional_median(samples: list[float]) -> str:
    """
    Format median of samples, or ``N/A`` when empty.

    Args:
      samples (list[float]): Numeric samples.

    Returns:
      str: Median formatted to 3 decimals, or ``N/A``.

    Examples:
      >>> _fmt_optional_median([])  # doctest: +SKIP
    """
    if not samples:
        return "N/A"
    return f"{median(samples):.3f}"


def _normalize_iso_timestamp(text: str) -> str:
    """
    Truncate nanosecond fractions for Python <3.11 fromisoformat compatibility.
    
    Args:
      text (str): String for text.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _normalize_iso_timestamp("x")  # doctest: +SKIP
    """
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$",
        text,
    )
    if not match:
        return text
    base, frac, suffix = match.group(1), match.group(2), match.group(3) or ""
    if frac:
        return f"{base}.{frac[:6]}{suffix}"
    return text


def _parse_log_timestamp(raw: str) -> Optional[datetime]:
    """
    Internal helper to parse the log timestamp.
    
    Args:
      raw (str): String for raw.
    
    Returns:
      Optional[datetime]: Optional[datetime] — the result, or None when
      unavailable.
    
    Examples:
      >>> _parse_log_timestamp("x")  # doctest: +SKIP
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = _normalize_iso_timestamp(text)
    if len(text) >= 5 and text[-3] != ":" and (text[-5] in "+-") and text[-2:].isdigit():
        text = text[:-2] + ":" + text[-2:]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_log_line(line: str) -> str:
    """
    Internal helper to normalize the log line.
    
    Args:
      line (str): String for line.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _normalize_log_line("x")  # doctest: +SKIP
    """
    text = line.rstrip("\r\n")
    text = _ANSI_ESCAPE_RE.sub("", text)
    return text.lstrip()


def _strip_log_prefix(line: str) -> tuple[Optional[datetime], str]:
    """
    Internal helper to handle strip log prefix.
    
    Args:
      line (str): String for line.
    
    Returns:
      tuple[Optional[datetime], str]: tuple[Optional[datetime], str] produced
      by this call.
    
    Examples:
      >>> _strip_log_prefix("x")  # doctest: +SKIP
    """
    text = _normalize_log_line(line)
    for pattern in (
        _LOG_TS_PIPE_RE,
        _LOG_TS_CONTAINER_PIPE_RE,
        _LOG_TS_CONTAINER_FIRST_RE,
        _LOG_TS_LEADING_RE,
    ):
        match = pattern.match(text)
        if match:
            return _parse_log_timestamp(match.group("ts")), match.group("body")
    return None, text


def _record_timestamp(metrics: LogMetrics, ts: Optional[datetime]) -> None:
    """
    Internal helper to handle record timestamp.
    
    Args:
      metrics (LogMetrics): Metrics.
      ts (Optional[datetime]): Ts, or None when absent.
    
    Returns:
      None
    
    Examples:
      >>> _record_timestamp(None, None)  # doctest: +SKIP
    """
    if ts is None:
        return
    metrics.timestamped_lines += 1
    if metrics.first_ts is None or ts < metrics.first_ts:
        metrics.first_ts = ts
    if metrics.last_ts is None or ts > metrics.last_ts:
        metrics.last_ts = ts


def _find_ingest_start_cutoff(lines: Iterable[str]) -> Optional[int]:
    """
    Return index of last ingest-start marker line (inclusive), or None.
    
    Prefer ``startup ingest gate cleared`` so later catch-up ``pending rescan
    done`` lines do not shrink the window. Fall back to ``chunk imap start``.
    
    Args:
      lines (Iterable[str]): Lines.
    
    Returns:
      Optional[int]: Optional[int] — the result, or None when unavailable.
    
    Examples:
      >>> _find_ingest_start_cutoff(None)  # doctest: +SKIP
    """
    last_gate: Optional[int] = None
    last_fallback: Optional[int] = None
    for idx, line in enumerate(lines):
        _, body = _strip_log_prefix(line)
        if any(marker in body for marker in _BOOT_MARKERS):
            last_gate = idx
        elif any(marker in body for marker in _INGEST_START_FALLBACK_MARKERS):
            last_fallback = idx
    if last_gate is not None:
        return last_gate
    return last_fallback


def _find_boot_cutoff(lines: Iterable[str]) -> Optional[int]:
    """
    Alias for :func:`_find_ingest_start_cutoff` (historical name).
    
    Args:
      lines (Iterable[str]): Lines.
    
    Returns:
      Optional[int]: Optional[int] — the result, or None when unavailable.
    
    Examples:
      >>> _find_boot_cutoff(None)  # doctest: +SKIP
    """
    return _find_ingest_start_cutoff(lines)


def _iter_filtered_lines(
  lines: Iterable[str],
  *,
  since_minutes: Optional[float],
  exclude_startup: bool,
  reference_end: Optional[datetime] = None,
) -> Iterator[tuple[Optional[datetime], str]]:
    """
    Internal helper to iterate over the filtered lines.
    
    Args:
      lines (Iterable[str]): Lines.
      since_minutes (Optional[float]): Since minutes, or None when absent.
      exclude_startup (bool): Boolean flag for exclude startup.
      reference_end (Optional[datetime]): Reference end, or None when absent.
    
    Yields:
      Iterator[tuple[Optional[datetime], str]]:
      Iterator[tuple[Optional[datetime], str]] produced by this call.
    
    Examples:
      >>> _iter_filtered_lines(None, None, True, None)  # doctest: +SKIP
    """
    materialized = list(lines)
    start_idx = 0
    if exclude_startup:
        boot_idx = _find_ingest_start_cutoff(materialized)
        if boot_idx is not None:
            start_idx = boot_idx

    cutoff: Optional[datetime] = None
    if since_minutes is not None and since_minutes > 0:
        end = reference_end
        if end is None:
            for line in materialized[start_idx:]:
                ts, _ = _strip_log_prefix(line)
                if ts is not None:
                    end = ts
            if end is None:
                end = datetime.now(timezone.utc)
        cutoff = end - timedelta(minutes=since_minutes)

    for line in materialized[start_idx:]:
        ts, body = _strip_log_prefix(line)
        if cutoff is not None:
            if ts is None or ts < cutoff:
                continue
        yield ts, body


def parse_log_lines(
  lines: Iterable[str],
  *,
  since_minutes: Optional[float] = None,
  exclude_startup: bool = True,
  boot_only: Optional[bool] = None,
) -> LogMetrics:
    """
    Parse the log lines.
    
    Args:
      lines (Iterable[str]): Lines.
      since_minutes (Optional[float]): Since minutes, or None when absent.
      exclude_startup (bool): Boolean flag for exclude startup.
      boot_only (Optional[bool]): Boot only, or None when absent.
    
    Returns:
      LogMetrics: LogMetrics produced by this call.
    
    Examples:
      >>> parse_log_lines(None, None, True, None)  # doctest: +SKIP
    """
    if boot_only is not None:
        exclude_startup = bool(boot_only)
    metrics = LogMetrics()
    for ts, body in _iter_filtered_lines(
        lines,
        since_minutes=since_minutes,
        exclude_startup=exclude_startup,
    ):
        _record_timestamp(metrics, ts)

        unlink_match = _LISTEND_UNLINKS_RE.search(body)
        if unlink_match:
            metrics.listend_unlink_samples.append(
                (ts, int(unlink_match.group(1))),
            )

        if _FULL_INGEST_RE.search(body):
            _record_full_ingest(metrics, body)

        immediate_match = _CHUNK_IMMEDIATE_RE.search(body)
        if immediate_match:
            metrics.archive_immediate_sum += int(immediate_match.group(1))

        finalize_match = _ARCHIVE_FINALIZE_RE.search(body)
        if finalize_match:
            metrics.archive_finalize_sum += int(finalize_match.group(1))

        if ts is not None:
            rescan_match = _PENDING_RESCAN_RE.search(body)
            if rescan_match:
                metrics.backlog_disk_samples.append((ts, int(rescan_match.group(1))))
            truncate_match = _PENDING_TRUNCATE_RE.search(body)
            if truncate_match:
                metrics.backlog_disk_samples.append(
                    (ts, int(truncate_match.group(1))),
                )
                metrics.truncate_max_samples.append(int(truncate_match.group(2)))
            backlog_match = _THROUGHPUT_BACKLOG_RE.search(body)
            if backlog_match:
                metrics.backlog_throughput_samples.append(
                    (ts, int(backlog_match.group(1))),
                )

    metrics.listend_unlink_sum = _nonoverlapping_listend_unlink_sum(
        metrics.listend_unlink_samples,
    )
    return metrics


def _report_count_from_unlink_sum(
  lines: Iterable[str],
  *,
  since_minutes: Optional[float] = None,
  exclude_startup: bool = True,
  boot_only: Optional[bool] = None,
) -> int:
    """
    Internal helper to handle report count from unlink sum.
    
    Args:
      lines (Iterable[str]): Lines.
      since_minutes (Optional[float]): Since minutes, or None when absent.
      exclude_startup (bool): Boolean flag for exclude startup.
      boot_only (Optional[bool]): Boot only, or None when absent.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> _report_count_from_unlink_sum(None, None, True, None)  # doctest: +SKIP
    """
    if boot_only is not None:
        exclude_startup = bool(boot_only)
    count = 0
    for _ts, body in _iter_filtered_lines(
        lines,
        since_minutes=since_minutes,
        exclude_startup=exclude_startup,
    ):
        if _LISTEND_UNLINKS_RE.search(body):
            count += 1
    return count


def resolve_window_minutes(
  metrics: LogMetrics,
  *,
  lines: Optional[Iterable[str]] = None,
  since_minutes: Optional[float] = None,
  exclude_startup: bool = True,
  boot_only: Optional[bool] = None,
) -> float:
    """
    Resolve the window minutes.
    
    Args:
      metrics (LogMetrics): Metrics.
      lines (Optional[Iterable[str]]): Lines, or None when absent.
      since_minutes (Optional[float]): Since minutes, or None when absent.
      exclude_startup (bool): Boolean flag for exclude startup.
      boot_only (Optional[bool]): Boot only, or None when absent.
    
    Returns:
      float: float produced by this call.
    
    Examples:
      >>> resolve_window_minutes(None, None, None, True, None)  # doctest: +SKIP
    """
    if boot_only is not None:
        exclude_startup = bool(boot_only)
    if since_minutes is not None and since_minutes > 0:
        return float(since_minutes)
    if metrics.first_ts is not None and metrics.last_ts is not None:
        span = (metrics.last_ts - metrics.first_ts).total_seconds() / 60.0
        if span >= 1.0:
            return span
    if lines is not None:
        report_count = _report_count_from_unlink_sum(
            lines,
            since_minutes=since_minutes,
            exclude_startup=exclude_startup,
        )
        if report_count > 0:
            return report_count * LISTEND_REPORT_WINDOW_MINUTES
    return 0.0


def _backlog_at_start(metrics: LogMetrics) -> Optional[int]:
    """
    First uncapped on-disk pending sample (rescan or truncate).
    
    Args:
      metrics (LogMetrics): Metrics.
    
    Returns:
      Optional[int]: Optional[int] — the result, or None when unavailable.
    
    Examples:
      >>> _backlog_at_start(None)  # doctest: +SKIP
    """
    if metrics.backlog_disk_samples:
        return metrics.backlog_disk_samples[0][1]
    return None


def _backlog_latest(metrics: LogMetrics) -> Optional[int]:
    """
    Latest uncapped on-disk pending sample (rescan or truncate).
    
    Args:
      metrics (LogMetrics): Metrics.
    
    Returns:
      Optional[int]: Optional[int] — the result, or None when unavailable.
    
    Examples:
      >>> _backlog_latest(None)  # doctest: +SKIP
    """
    if metrics.backlog_disk_samples:
        return metrics.backlog_disk_samples[-1][1]
    return None


def _disk_sample_count(metrics: LogMetrics) -> int:
    """
    Internal helper to handle disk sample count.
    
    Args:
      metrics (LogMetrics): Metrics.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> _disk_sample_count(None)  # doctest: +SKIP
    """
    return len(metrics.backlog_disk_samples)


def _queue_depth_at_start(metrics: LogMetrics) -> Optional[int]:
    """
    Internal helper to handle queue depth at start.
    
    Args:
      metrics (LogMetrics): Metrics.
    
    Returns:
      Optional[int]: Optional[int] — the result, or None when unavailable.
    
    Examples:
      >>> _queue_depth_at_start(None)  # doctest: +SKIP
    """
    if metrics.backlog_throughput_samples:
        return metrics.backlog_throughput_samples[0][1]
    return None


def _queue_depth_latest(metrics: LogMetrics) -> Optional[int]:
    """
    Internal helper to handle queue depth latest.
    
    Args:
      metrics (LogMetrics): Metrics.
    
    Returns:
      Optional[int]: Optional[int] — the result, or None when unavailable.
    
    Examples:
      >>> _queue_depth_latest(None)  # doctest: +SKIP
    """
    if metrics.backlog_throughput_samples:
        return metrics.backlog_throughput_samples[-1][1]
    return None


def _ratio_and_verdict(
  listend_rate: float,
  sync_rate: float,
) -> tuple[str, str]:
    """
    Internal helper to handle ratio and verdict.
    
    Args:
      listend_rate (float): Floating-point value for listend rate.
      sync_rate (float): Floating-point value for sync rate.
    
    Returns:
      tuple[str, str]: tuple[str, str] produced by this call.
    
    Examples:
      >>> _ratio_and_verdict(0, 0)  # doctest: +SKIP
    """
    if sync_rate <= 0:
        if listend_rate <= 0:
            return "N/A", "N/A"
        return "inf", "LOSING"
    ratio = listend_rate / sync_rate
    if abs(ratio - 1.0) <= EVEN_RATIO_TOLERANCE:
        verdict = "EVEN"
    elif ratio > 1.0:
        verdict = "LOSING"
    else:
        verdict = "WINNING"
    return f"{ratio:.4f}", verdict


def _nonoverlapping_listend_unlink_sum(
  samples: list[tuple[Optional[datetime], int]],
  *,
  window_minutes: float = LISTEND_REPORT_WINDOW_MINUTES,
) -> int:
    """
    Sum rolling listend unlink counts without double-counting overlaps.

    Each listend line reports closures in the trailing ``window_minutes``.
    Keep a sample only when it starts a new non-overlapping window (gap
    >= ``window_minutes`` from the previous kept sample). Untimestamped
    samples fall back to a raw sum because the caller already treats each
    report as a full window via ``report_count * window_minutes``.

    Args:
      samples (list[tuple[Optional[datetime], int]]): Ordered (timestamp,
        unlink_count) samples from listend reports.
      window_minutes (float): Rolling report window length in minutes.

    Returns:
      int: Non-overlapping unlink sum used for arrival-rate estimates.

    Examples:
      >>> from datetime import datetime, timezone
      >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
      >>> _nonoverlapping_listend_unlink_sum(
      ...     [(t0, 10), (t0 + timedelta(minutes=1), 10)],
      ... )
      10
    """
    if not samples:
        return 0
    if any(ts is None for ts, _count in samples):
        return sum(int(count) for _ts, count in samples)
    window = timedelta(minutes=float(window_minutes))
    kept_sum = 0
    last_kept: Optional[datetime] = None
    for ts, count in samples:
        assert ts is not None  # guarded above
        if last_kept is None or (ts - last_kept) >= window:
            kept_sum += int(count)
            last_kept = ts
    return kept_sum


def _eta_hours(backlog: Optional[int], drain_per_min: float) -> str:
    """
    Format drain ETA hours, or N/A when backlog/rate cannot support a finish.

    Unknown backlog (``None``) must not become a false zero-hour finish.
    A measured empty backlog (``<= 0``) returns ``\"0\"``.

    Args:
      backlog (Optional[int]): Uncapped disk backlog, or None when absent.
      drain_per_min (float): Positive drain rate in files per minute.

    Returns:
      str: Hours to drain as a fixed-point string, ``\"0\"``, or ``\"N/A\"``.

    Examples:
      >>> _eta_hours(None, 1.0)
      'N/A'
      >>> _eta_hours(0, 1.0)
      '0'
      >>> _eta_hours(120, 2.0)
      '1.00'
    """
    if backlog is None:
        return "N/A"
    if backlog <= 0:
        return "0"
    if drain_per_min <= 0:
        return "N/A"
    return f"{backlog / drain_per_min / 60.0:.2f}"


def _fmt_rate(value: float) -> str:
    """
    Internal helper to handle fmt rate.
    
    Args:
      value (float): Floating-point value for value.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _fmt_rate(0)  # doctest: +SKIP
    """
    return f"{value:.4f}"


def _fmt_optional_int(value: Optional[int]) -> str:
    """
    Internal helper to handle fmt optional int.
    
    Args:
      value (Optional[int]): Value, or None when absent.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _fmt_optional_int(None)  # doctest: +SKIP
    """
    return "N/A" if value is None else str(value)


def _fmt_ts(value: Optional[datetime]) -> str:
    """
    Internal helper to handle fmt ts.
    
    Args:
      value (Optional[datetime]): Value, or None when absent.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _fmt_ts(None)  # doctest: +SKIP
    """
    if value is None:
        return "N/A"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_local_ts(value: Optional[datetime]) -> str:
    """
    Format as local date and time with numeric UTC offset (e.g. 2026-07-10.
    
      14:32:15 -0500).
    
    Args:
      value (Optional[datetime]): Value, or None when absent.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _fmt_local_ts(None)  # doctest: +SKIP
    """
    if value is None:
        return "N/A"
    local = value.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S %z")


def _parse_eta_hours(text: str) -> Optional[float]:
    """
    Internal helper to parse the eta hours.
    
    Args:
      text (str): String for text.
    
    Returns:
      Optional[float]: Optional[float] — the result, or None when unavailable.
    
    Examples:
      >>> _parse_eta_hours("x")  # doctest: +SKIP
    """
    if text in ("N/A", ""):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _estimated_finish_local(
  log_end: Optional[datetime],
  *,
  eta_empirical: str,
  eta_full_ingest: str,
  eta_archive_done: str,
) -> tuple[str, str]:
    """
    Return (local finish stamp, eta field name) from the first usable ETA.
    
    Preference: empirical drain, then full ingest net rate, then archive-done
      net rate.
    Anchor is log_end when present, otherwise current UTC.
    
    Args:
      log_end (Optional[datetime]): Log end, or None when absent.
      eta_empirical (str): String for eta empirical.
      eta_full_ingest (str): String for eta full ingest.
      eta_archive_done (str): String for eta archive done.
    
    Returns:
      tuple[str, str]: tuple[str, str] produced by this call.
    
    Examples:
      >>> _estimated_finish_local(None, "x", "x", "x")  # doctest: +SKIP
    """
    base = log_end if log_end is not None else datetime.now(timezone.utc)
    for basis, eta_s in (
        ("eta_hours_empirical", eta_empirical),
        ("eta_hours_full_ingest", eta_full_ingest),
        ("eta_hours_archive_done", eta_archive_done),
    ):
        hours = _parse_eta_hours(eta_s)
        if hours is None:
            continue
        finish = base + timedelta(hours=hours)
        return _fmt_local_ts(finish), basis
    return "N/A", "N/A"


def build_outcomes(
  metrics: LogMetrics,
  *,
  window_minutes: float,
) -> dict[str, str]:
    """
    Build the outcomes.
    
    Args:
      metrics (LogMetrics): Metrics.
      window_minutes (float): Floating-point value for window minutes.
    
    Returns:
      dict[str, str]: dict[str, str] produced by this call.
    
    Raises:
      ValueError: Raised when ``build_outcomes`` hits a ``ValueError`` failure
      path.
    
    Examples:
      >>> build_outcomes(None, 0)  # doctest: +SKIP
    """
    if window_minutes < 1.0:
        raise ValueError("insufficient log window for rate calculation")

    metrics.listend_unlink_sum = _nonoverlapping_listend_unlink_sum(
        metrics.listend_unlink_samples,
    )
    listend_rate = metrics.listend_unlink_sum / window_minutes
    ingest_rate = metrics.full_ingest_count / window_minutes
    archive_done_count = metrics.archive_immediate_sum + metrics.archive_finalize_sum
    archive_rate = archive_done_count / window_minutes

    ratio_ingest, verdict_ingest = _ratio_and_verdict(listend_rate, ingest_rate)
    ratio_archive, verdict_archive = _ratio_and_verdict(listend_rate, archive_rate)

    backlog_start = _backlog_at_start(metrics)
    backlog_latest = _backlog_latest(metrics)
    queue_start = _queue_depth_at_start(metrics)
    queue_latest = _queue_depth_latest(metrics)
    disk_n = _disk_sample_count(metrics)
    elapsed_hours = 0.0
    elapsed_minutes = 0.0
    if metrics.first_ts is not None and metrics.last_ts is not None:
        elapsed_hours = (metrics.last_ts - metrics.first_ts).total_seconds() / 3600.0
        elapsed_minutes = elapsed_hours * 60.0

    empirical_drain = 0.0
    drained = None
    pct_complete = "N/A"
    # Require ≥2 disk samples so drain is not invented from a single census point.
    if (
        disk_n >= 2
        and backlog_start is not None
        and backlog_latest is not None
    ):
        drained = backlog_start - backlog_latest
        if backlog_start > 0:
            pct_complete = f"{(drained / backlog_start) * 100.0:.2f}"
        if elapsed_minutes >= 1.0:
            empirical_drain = drained / elapsed_minutes

    net_ingest = ingest_rate - listend_rate
    net_archive = archive_rate - listend_rate

    eta_empirical = _eta_hours(backlog_latest, empirical_drain)
    eta_full_ingest = _eta_hours(backlog_latest, net_ingest)
    eta_archive_done = _eta_hours(backlog_latest, net_archive)
    finish_local, finish_basis = _estimated_finish_local(
        metrics.last_ts,
        eta_empirical=eta_empirical,
        eta_full_ingest=eta_full_ingest,
        eta_archive_done=eta_archive_done,
    )

    mib_per_min = 0.0
    if window_minutes > 0:
        mib_per_min = (metrics.full_ingest_bytes / float(_MIB_BYTES)) / window_minutes

    outcomes = {
        "window_minutes": f"{window_minutes:.2f}",
        "listend_closed_per_min": _fmt_rate(listend_rate),
        "sync_full_ingest_per_min": _fmt_rate(ingest_rate),
        "sync_full_ingest_mib_per_min": _fmt_rate(mib_per_min),
        "sync_archive_done_per_min": _fmt_rate(archive_rate),
        "ratio_listend_over_full_ingest": ratio_ingest,
        "verdict_full_ingest": verdict_ingest,
        "ratio_listend_over_archive_done": ratio_archive,
        "verdict_archive_done": verdict_archive,
        "backlog_gap_full_ingest_per_min": _fmt_rate(listend_rate - ingest_rate),
        "backlog_gap_archive_done_per_min": _fmt_rate(listend_rate - archive_rate),
        "ingest_start_utc": _fmt_ts(metrics.first_ts),
        "log_end_utc": _fmt_ts(metrics.last_ts),
        "elapsed_hours": f"{elapsed_hours:.3f}",
        "backlog_at_start": _fmt_optional_int(backlog_start),
        "backlog_latest": _fmt_optional_int(backlog_latest),
        "backlog_drained_since_start": _fmt_optional_int(drained),
        "pct_complete_since_start": pct_complete,
        "ingest_queue_depth_at_start": _fmt_optional_int(queue_start),
        "ingest_queue_depth_latest": _fmt_optional_int(queue_latest),
        "empirical_drain_per_min": _fmt_rate(empirical_drain),
        "eta_hours_empirical": eta_empirical,
        "eta_hours_full_ingest": eta_full_ingest,
        "eta_hours_archive_done": eta_archive_done,
        "estimated_finish_local": finish_local,
        "estimated_finish_basis": finish_basis,
    }
    for tier in _SIZE_TIER_NAMES:
        count = int(metrics.full_ingest_count_by_tier.get(tier, 0))
        tier_rate = (count / window_minutes) if window_minutes > 0 else 0.0
        outcomes[f"tier_{tier}_count"] = str(count)
        outcomes[f"tier_{tier}_per_min"] = _fmt_rate(tier_rate)
        outcomes[f"tier_{tier}_median_elapsed_s"] = _fmt_optional_median(
            metrics.full_ingest_elapsed_by_tier.get(tier, []),
        )
        outcomes[f"tier_{tier}_median_postgres_s"] = _fmt_optional_median(
            metrics.full_ingest_postgres_by_tier.get(tier, []),
        )
        elapsed_samples = metrics.full_ingest_elapsed_by_tier.get(tier, [])
        postgres_samples = metrics.full_ingest_postgres_by_tier.get(tier, [])
        fracs: list[float] = []
        for e_s, p_s in zip(elapsed_samples, postgres_samples):
            if e_s and e_s > 0:
                fracs.append(float(p_s) / float(e_s))
        outcomes[f"tier_{tier}_median_postgres_frac"] = _fmt_optional_median(fracs)
        phase_map = metrics.full_ingest_phases_by_tier.get(tier, {})
        for tok in (_WRITE_PHASE_TOKEN_NAMES + _PARSE_HOLD_TOKEN_NAMES):
            outcomes[f"tier_{tier}_median_{tok}"] = _fmt_optional_median(
                phase_map.get(tok, []),
            )

    # Overnight decision pack (mid-tier 64mib_1gib; fall back to lt_64mib).
    mid_tier = _MID_TIER_NAME
    if int(metrics.full_ingest_count_by_tier.get(mid_tier, 0)) == 0:
        mid_tier = "lt_64mib"
    mid_elapsed = _median_or_none(
        metrics.full_ingest_elapsed_by_tier.get(mid_tier, []),
    )
    mid_postgres = _median_or_none(
        metrics.full_ingest_postgres_by_tier.get(mid_tier, []),
    )
    mid_postgres_frac = None
    if mid_elapsed and mid_elapsed > 0 and mid_postgres is not None:
        mid_postgres_frac = mid_postgres / mid_elapsed
    mid_phases = metrics.full_ingest_phases_by_tier.get(mid_tier, {})
    write_medians = {
        tok: _median_or_none(mid_phases.get(tok, []))
        for tok in _WRITE_PHASE_TOKEN_NAMES
    }
    parse_medians = {
        tok: _median_or_none(mid_phases.get(tok, []))
        for tok in _PARSE_HOLD_TOKEN_NAMES
        if tok not in ("stages_sum_s", "parse_unaccounted_s", "build_df_s")
    }
    write_sum = sum(v for v in write_medians.values() if v is not None)
    exec_like = 0.0
    for tok in ("db_execute_s", "copy_s", "conflict_insert_s"):
        if write_medians.get(tok) is not None:
            exec_like += float(write_medians[tok])
    write_share = (
        (write_sum / float(mid_elapsed)) if mid_elapsed and mid_elapsed > 0 else 0.0
    )
    exec_of_write = (exec_like / write_sum) if write_sum > 0 else 0.0
    mid_write_dominates = bool(
        (mid_postgres_frac is not None and mid_postgres_frac >= 0.35)
        or (write_share >= 0.35 and exec_of_write >= 0.5),
    )
    mid_top_parse = None
    top_val = -1.0
    for tok, val in parse_medians.items():
        if val is not None and val > top_val:
            top_val = float(val)
            mid_top_parse = tok
    if mid_top_parse is not None and (
        mid_postgres_frac is not None and mid_postgres_frac >= 0.35
    ):
        # Prefer write branch when postgres frac is high.
        mid_top_parse = None
    unaccounted_med = _median_or_none(
        mid_phases.get("parse_unaccounted_s", []),
    )
    if unaccounted_med is None:
        parse_unaccounted_dominates = False
    elif mid_top_parse is not None:
        parse_unaccounted_dominates = float(unaccounted_med) >= float(top_val)
    else:
        parse_unaccounted_dominates = float(unaccounted_med) > 0.0
    named_parse_sample_n = sum(
        len(mid_phases.get(tok, []))
        for tok in _PARSE_HOLD_TOKEN_NAMES
        if tok not in ("stages_sum_s", "parse_unaccounted_s", "build_df_s")
    )
    write_sample_n = sum(
        len(mid_phases.get(tok, [])) for tok in _WRITE_PHASE_TOKEN_NAMES
    )
    telem_incomplete = bool(named_parse_sample_n > 0 and write_sample_n == 0)
    if telem_incomplete:
        print(
            "WARN: mid-tier parse holds present but no write-phase tokens "
            "(orm_materialize_s/db_execute_s/copy_s/…); "
            "decision_next=telem_incomplete_re_soak — enable "
            "sync_ingest_telemetry=yes on redeploy",
            file=sys.stderr,
        )
    total_elapsed_samples = sum(
        len(metrics.full_ingest_elapsed_by_tier.get(t, []))
        for t in _SIZE_TIER_NAMES
    )
    large_elapsed_n = len(
        metrics.full_ingest_elapsed_by_tier.get("1_4gib", []),
    ) + len(metrics.full_ingest_elapsed_by_tier.get("ge_4gib", []))
    ge_share = (
        (large_elapsed_n / float(total_elapsed_samples))
        if total_elapsed_samples else None
    )
    outcomes["mid_tier_name"] = mid_tier
    outcomes["mid_tier_median_postgres_frac"] = (
        f"{mid_postgres_frac:.3f}" if mid_postgres_frac is not None else "N/A"
    )
    outcomes["mid_tier_top_parse_hold"] = mid_top_parse or "N/A"
    outcomes["mid_tier_write_exec_dominates"] = (
        "yes" if mid_write_dominates else "no"
    )
    outcomes["mid_tier_telem_incomplete"] = (
        "yes" if telem_incomplete else "no"
    )
    outcomes["mid_tier_parse_unaccounted_dominates"] = (
        "yes" if parse_unaccounted_dominates else "no"
    )
    outcomes["decision_next"] = _decision_next(
        ratio_ingest=ratio_ingest,
        window_minutes=window_minutes,
        metrics=metrics,
        mid_postgres_frac=mid_postgres_frac,
        mid_top_parse_hold=mid_top_parse if not mid_write_dominates else None,
        mid_write_dominates=mid_write_dominates,
        ge_1gib_wall_share=ge_share,
        telem_incomplete=telem_incomplete,
        parse_unaccounted_dominates=(
            parse_unaccounted_dominates and not mid_write_dominates
        ),
    )
    return outcomes


def emit_warnings(metrics: LogMetrics, window_minutes: float) -> None:
    """
    Emit warnings.
    
    Args:
      metrics (LogMetrics): Metrics.
      window_minutes (float): Floating-point value for window minutes.
    
    Returns:
      None
    
    Examples:
      >>> emit_warnings(None, 0)  # doctest: +SKIP
    """
    if metrics.timestamped_lines == 0:
        print(
            "WARN: no timestamped log lines; rates use listend-report window fallback",
            file=sys.stderr,
        )
    if metrics.listend_unlink_sum == 0:
        print("WARN: no listend unlink reports found", file=sys.stderr)
    if metrics.full_ingest_count == 0:
        print("WARN: no full ingest lines found", file=sys.stderr)
    disk_n = _disk_sample_count(metrics)
    if disk_n == 0:
        print(
            "WARN: no disk_pending samples found "
            "(pending rescan done / truncated pending=); ETA may be N/A",
            file=sys.stderr,
        )
    elif disk_n == 1:
        print(
            "WARN: insufficient disk_pending samples for drain "
            "(need >=2 of rescan done / truncated pending=)",
            file=sys.stderr,
        )
    queue_latest = _queue_depth_latest(metrics)
    backlog_latest = _backlog_latest(metrics)
    if (
        queue_latest is not None
        and backlog_latest is not None
        and queue_latest > 0
        and backlog_latest >= queue_latest * _QUEUE_SATURATION_DISK_FACTOR
    ):
        watermark = queue_latest
        if metrics.truncate_max_samples:
            watermark = metrics.truncate_max_samples[-1]
        if queue_latest >= watermark:
            print(
                "WARN: ingest queue depth saturated at %d while disk_pending=%d "
                "(queue depth is not disk backlog)"
                % (queue_latest, backlog_latest),
                file=sys.stderr,
            )
    if window_minutes < 60:
        print(
            "WARN: short log window (%.1f min); rates and ETA are noisy"
            % window_minutes,
            file=sys.stderr,
        )


def format_stdout(outcomes: dict[str, str]) -> str:
    """
    Format the stdout.
    
    Args:
      outcomes (dict[str, str]): Mapping for outcomes.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> format_stdout({})  # doctest: +SKIP
    """
    order = (
        "window_minutes",
        "listend_closed_per_min",
        "sync_full_ingest_per_min",
        "sync_full_ingest_mib_per_min",
        "sync_archive_done_per_min",
        "ratio_listend_over_full_ingest",
        "verdict_full_ingest",
        "ratio_listend_over_archive_done",
        "verdict_archive_done",
        "backlog_gap_full_ingest_per_min",
        "backlog_gap_archive_done_per_min",
        "ingest_start_utc",
        "log_end_utc",
        "elapsed_hours",
        "backlog_at_start",
        "backlog_latest",
        "backlog_drained_since_start",
        "pct_complete_since_start",
        "ingest_queue_depth_at_start",
        "ingest_queue_depth_latest",
        "empirical_drain_per_min",
        "eta_hours_empirical",
        "eta_hours_full_ingest",
        "eta_hours_archive_done",
        "estimated_finish_local",
        "estimated_finish_basis",
        "mid_tier_name",
        "mid_tier_median_postgres_frac",
        "mid_tier_top_parse_hold",
        "mid_tier_write_exec_dominates",
        "mid_tier_telem_incomplete",
        "mid_tier_parse_unaccounted_dominates",
        "decision_next",
    )
    tier_keys: list[str] = []
    for tier in _SIZE_TIER_NAMES:
        tier_keys.extend(
            (
                f"tier_{tier}_count",
                f"tier_{tier}_per_min",
                f"tier_{tier}_median_elapsed_s",
                f"tier_{tier}_median_postgres_s",
                f"tier_{tier}_median_postgres_frac",
            )
        )
        for tok in ("db_execute_s", "copy_s", "conflict_insert_s", "orm_materialize_s", "feed_s", "collapse_s", "build_df_s"):
            tier_keys.append(f"tier_{tier}_median_{tok}")
    return "\n".join(
        f"{key}={outcomes[key]}" for key in (*order, *tier_keys) if key in outcomes
    )


def analyze_lines(
  lines: Iterable[str],
  *,
  since_minutes: Optional[float] = None,
  exclude_startup: bool = True,
  boot_only: Optional[bool] = None,
) -> dict[str, str]:
    """
    Analyze lines.
    
    Args:
      lines (Iterable[str]): Lines.
      since_minutes (Optional[float]): Since minutes, or None when absent.
      exclude_startup (bool): Boolean flag for exclude startup.
      boot_only (Optional[bool]): Boot only, or None when absent.
    
    Returns:
      dict[str, str]: dict[str, str] produced by this call.
    
    Raises:
      ValueError: Raised when ``analyze_lines`` hits a ``ValueError`` failure
      path.
    
    Examples:
      >>> analyze_lines(None, None, True, None)  # doctest: +SKIP
    """
    if boot_only is not None:
        exclude_startup = bool(boot_only)
    line_list = list(lines)
    metrics = parse_log_lines(
        line_list,
        since_minutes=since_minutes,
        exclude_startup=exclude_startup,
    )
    window = resolve_window_minutes(
        metrics,
        lines=line_list,
        since_minutes=since_minutes,
        exclude_startup=exclude_startup,
    )
    if window < 1.0:
        raise ValueError(
            "insufficient log window for rate calculation "
            "(need timestamps spanning >=1 min or listend idle reports)"
        )
    emit_warnings(metrics, window)
    return build_outcomes(metrics, window_minutes=window)


def _fetch_compose_logs(compose_argv: list[str]) -> list[str]:
    """
    Internal helper to fetch the compose logs.
    
    Args:
      compose_argv (list[str]): Sequence for compose argv.
    
    Returns:
      list[str]: list[str] produced by this call.
    
    Raises:
      RuntimeError: Raised when ``_fetch_compose_logs`` hits a
      ``RuntimeError`` failure path.
    
    Examples:
      >>> _fetch_compose_logs([])  # doctest: +SKIP
    """
    cmd = compose_argv + ["logs", "--timestamps", "pipeline"]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "compose logs failed (exit %d): %s"
            % (proc.returncode, (proc.stderr or proc.stdout or "").strip())
        )
    return proc.stdout.splitlines()


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """
    Internal helper to parse the args.
    
    Args:
      argv (Optional[list[str]]): Argv, or None when absent.
    
    Returns:
      argparse.Namespace: argparse.Namespace produced by this call.
    
    Examples:
      >>> _parse_args(None)  # doctest: +SKIP
    """
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--log-file",
        metavar="PATH",
        help="Read pipeline log from file (full dump; do not use compose --tail)",
    )
    src.add_argument(
        "--fetch-compose",
        action="store_true",
        help="Run compose logs --timestamps pipeline (see --compose-cmd)",
    )
    parser.add_argument(
        "--compose-cmd",
        default="docker compose",
        help="Compose command prefix (default: 'docker compose')",
    )
    parser.add_argument(
        "--compose-project",
        default="",
        help="Optional -p project name for compose",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Optional -f compose file (repeatable)",
    )
    parser.add_argument(
        "--since-minutes",
        type=float,
        default=None,
        metavar="N",
        help="Only analyze log lines with timestamps in the last N minutes",
    )
    parser.add_argument(
        "--include-startup",
        action="store_true",
        help=(
            "Include pre-ingest startup lines in the measurement window "
            "(default starts at last 'startup ingest gate cleared')"
        ),
    )
    parser.add_argument(
        "--boot-only",
        action="store_true",
        help=(
            "Deprecated alias: startup is already excluded by default "
            "(same as omitting --include-startup)"
        ),
    )
    return parser.parse_args(argv)


def _read_input_lines(args: argparse.Namespace) -> list[str]:
    """
    Internal helper to read the input lines.
    
    Args:
      args (argparse.Namespace): Args.
    
    Returns:
      list[str]: list[str] produced by this call.
    
    Examples:
      >>> _read_input_lines(None)  # doctest: +SKIP
    """
    if args.fetch_compose:
        compose_argv = args.compose_cmd.split()
        if args.compose_project:
            compose_argv.extend(["-p", args.compose_project])
        for path in args.compose_file:
            compose_argv.extend(["-f", path])
        return _fetch_compose_logs(compose_argv)
    if args.log_file:
        with open(args.log_file, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    return sys.stdin.read().splitlines()


def main(argv: Optional[list[str]] = None) -> int:
    """
    Run this module's command-line entrypoint.
    
    Args:
      argv (Optional[list[str]]): Argv, or None when absent.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> main(None)  # doctest: +SKIP
    """
    args = _parse_args(argv)
    try:
        lines = _read_input_lines(args)
        outcomes = analyze_lines(
            lines,
            since_minutes=args.since_minutes,
            exclude_startup=not args.include_startup,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    print(format_stdout(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
