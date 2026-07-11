#!/usr/bin/env python3
"""Measure listend vs sync_timedb rates from pipeline logs.

Reads pipeline container logs (stdin, --log-file, or --fetch-compose) and prints
only summary outcome lines to stdout. Errors and caveats go to stderr.

Backlog fields (backlog_at_start / backlog_latest / drained / empirical ETA) use
**on-disk pending census only**:
  - ``sync_timedb: pending rescan done pending=N`` (uncapped before queue cap)
  - ``Pending stats file list truncated pending=N max=M`` (uncapped N at truncate)

Do **not** mix those with ``Throughput telemetry … backlog=N``, which is capped
in-memory ``len(pending_stats_files)`` (≤ sync_ingest_queue_max_size, often 2000).
Queue occupancy is reported separately as ingest_queue_depth_*.

``estimated_finish_local`` is log_end (or now) plus the first usable ETA among
empirical / full_ingest / archive_done, formatted in the host local timezone
(``YYYY-MM-DD HH:MM:SS ±HHMM``). ``estimated_finish_basis`` names which ETA was used.

Usage (from HPCPerfStats/):
  docker compose -f docker-compose.app.yaml logs --timestamps pipeline 2>&1 | python3 scripts/measure_pipeline_ingest_rate.py
  python3 scripts/measure_pipeline_ingest_rate.py --log-file /tmp/pipeline-full.log
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
_CHUNK_IMMEDIATE_RE = re.compile(
    r"sync_timedb: chunk ingest summary .*checkpoint_immediate_n=(\d+)"
)
_ARCHIVE_FINALIZE_RE = re.compile(
    r"sync_timedb: checkpoint deferred archive finalize count=(\d+)"
)
_PENDING_RESCAN_RE = re.compile(
    r"sync_timedb: pending rescan done pending=(\d+)"
)
_PENDING_TRUNCATE_RE = re.compile(
    r"Pending stats file list truncated pending=(\d+) max=(\d+)"
)
_THROUGHPUT_BACKLOG_RE = re.compile(
    r"Throughput telemetry: active_workers=\d+ backlog=(\d+)"
)
_BOOT_MARKERS = (
    "startup ingest gate cleared; ingest may begin",
    "sync_timedb: pending rescan done pending=",
)
# Disk pending ≫ queue depth by at least this factor → saturation WARN.
_QUEUE_SATURATION_DISK_FACTOR = 2

EVEN_RATIO_TOLERANCE = 0.02
LISTEND_REPORT_WINDOW_MINUTES = 10.0


@dataclass
class LogMetrics:
    listend_unlink_sum: int = 0
    full_ingest_count: int = 0
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
        """Alias kept for older tests/callers; same as disk samples."""
        return self.backlog_disk_samples


def _normalize_iso_timestamp(text: str) -> str:
    """Truncate nanosecond fractions for Python <3.11 fromisoformat compatibility."""
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
    text = line.rstrip("\r\n")
    text = _ANSI_ESCAPE_RE.sub("", text)
    return text.lstrip()


def _strip_log_prefix(line: str) -> tuple[Optional[datetime], str]:
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
    if ts is None:
        return
    metrics.timestamped_lines += 1
    if metrics.first_ts is None or ts < metrics.first_ts:
        metrics.first_ts = ts
    if metrics.last_ts is None or ts > metrics.last_ts:
        metrics.last_ts = ts


def _find_boot_cutoff(lines: Iterable[str]) -> Optional[int]:
    """Return index of last boot marker line (inclusive), or None."""
    last_idx: Optional[int] = None
    for idx, line in enumerate(lines):
        _, body = _strip_log_prefix(line)
        if any(marker in body for marker in _BOOT_MARKERS):
            last_idx = idx
    return last_idx


def _iter_filtered_lines(
    lines: Iterable[str],
    *,
    since_minutes: Optional[float],
    boot_only: bool,
    reference_end: Optional[datetime] = None,
) -> Iterator[tuple[Optional[datetime], str]]:
    materialized = list(lines)
    start_idx = 0
    if boot_only:
        boot_idx = _find_boot_cutoff(materialized)
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
    boot_only: bool = False,
) -> LogMetrics:
    metrics = LogMetrics()
    for ts, body in _iter_filtered_lines(
        lines,
        since_minutes=since_minutes,
        boot_only=boot_only,
    ):
        _record_timestamp(metrics, ts)

        unlink_match = _LISTEND_UNLINKS_RE.search(body)
        if unlink_match:
            metrics.listend_unlink_sum += int(unlink_match.group(1))

        if _FULL_INGEST_RE.search(body):
            metrics.full_ingest_count += 1

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

    return metrics


def _report_count_from_unlink_sum(
    lines: Iterable[str],
    *,
    since_minutes: Optional[float] = None,
    boot_only: bool = False,
) -> int:
    count = 0
    for _ts, body in _iter_filtered_lines(
        lines,
        since_minutes=since_minutes,
        boot_only=boot_only,
    ):
        if _LISTEND_UNLINKS_RE.search(body):
            count += 1
    return count


def resolve_window_minutes(
    metrics: LogMetrics,
    *,
    lines: Optional[Iterable[str]] = None,
    since_minutes: Optional[float] = None,
    boot_only: bool = False,
) -> float:
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
            boot_only=boot_only,
        )
        if report_count > 0:
            return report_count * LISTEND_REPORT_WINDOW_MINUTES
    return 0.0


def _backlog_at_start(metrics: LogMetrics) -> Optional[int]:
    """First uncapped on-disk pending sample (rescan or truncate)."""
    if metrics.backlog_disk_samples:
        return metrics.backlog_disk_samples[0][1]
    return None


def _backlog_latest(metrics: LogMetrics) -> Optional[int]:
    """Latest uncapped on-disk pending sample (rescan or truncate)."""
    if metrics.backlog_disk_samples:
        return metrics.backlog_disk_samples[-1][1]
    return None


def _disk_sample_count(metrics: LogMetrics) -> int:
    return len(metrics.backlog_disk_samples)


def _queue_depth_at_start(metrics: LogMetrics) -> Optional[int]:
    if metrics.backlog_throughput_samples:
        return metrics.backlog_throughput_samples[0][1]
    return None


def _queue_depth_latest(metrics: LogMetrics) -> Optional[int]:
    if metrics.backlog_throughput_samples:
        return metrics.backlog_throughput_samples[-1][1]
    return None


def _ratio_and_verdict(listend_rate: float, sync_rate: float) -> tuple[str, str]:
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


def _eta_hours(backlog: Optional[int], drain_per_min: float) -> str:
    if backlog is None or backlog <= 0:
        return "0"
    if drain_per_min <= 0:
        return "N/A"
    return f"{backlog / drain_per_min / 60.0:.2f}"


def _fmt_rate(value: float) -> str:
    return f"{value:.4f}"


def _fmt_optional_int(value: Optional[int]) -> str:
    return "N/A" if value is None else str(value)


def _fmt_ts(value: Optional[datetime]) -> str:
    if value is None:
        return "N/A"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_local_ts(value: Optional[datetime]) -> str:
    """Format as local date and time with numeric UTC offset (e.g. 2026-07-10 14:32:15 -0500)."""
    if value is None:
        return "N/A"
    local = value.astimezone()
    return local.strftime("%Y-%m-%d %H:%M:%S %z")


def _parse_eta_hours(text: str) -> Optional[float]:
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
    """Return (local finish stamp, eta field name) from the first usable ETA.

    Preference: empirical drain, then full ingest net rate, then archive-done net rate.
    Anchor is log_end when present, otherwise current UTC.
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
    if window_minutes < 1.0:
        raise ValueError("insufficient log window for rate calculation")

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

    outcomes = {
        "window_minutes": f"{window_minutes:.2f}",
        "listend_closed_per_min": _fmt_rate(listend_rate),
        "sync_full_ingest_per_min": _fmt_rate(ingest_rate),
        "sync_archive_done_per_min": _fmt_rate(archive_rate),
        "ratio_listend_over_full_ingest": ratio_ingest,
        "verdict_full_ingest": verdict_ingest,
        "ratio_listend_over_archive_done": ratio_archive,
        "verdict_archive_done": verdict_archive,
        "backlog_gap_full_ingest_per_min": _fmt_rate(listend_rate - ingest_rate),
        "backlog_gap_archive_done_per_min": _fmt_rate(listend_rate - archive_rate),
        "container_start_utc": _fmt_ts(metrics.first_ts),
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
    return outcomes


def emit_warnings(metrics: LogMetrics, window_minutes: float) -> None:
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
    order = (
        "window_minutes",
        "listend_closed_per_min",
        "sync_full_ingest_per_min",
        "sync_archive_done_per_min",
        "ratio_listend_over_full_ingest",
        "verdict_full_ingest",
        "ratio_listend_over_archive_done",
        "verdict_archive_done",
        "backlog_gap_full_ingest_per_min",
        "backlog_gap_archive_done_per_min",
        "container_start_utc",
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
    )
    return "\n".join(f"{key}={outcomes[key]}" for key in order)


def analyze_lines(
    lines: Iterable[str],
    *,
    since_minutes: Optional[float] = None,
    boot_only: bool = False,
) -> dict[str, str]:
    line_list = list(lines)
    metrics = parse_log_lines(
        line_list,
        since_minutes=since_minutes,
        boot_only=boot_only,
    )
    window = resolve_window_minutes(
        metrics,
        lines=line_list,
        since_minutes=since_minutes,
        boot_only=boot_only,
    )
    if window < 1.0:
        raise ValueError(
            "insufficient log window for rate calculation "
            "(need timestamps spanning >=1 min or listend idle reports)"
        )
    emit_warnings(metrics, window)
    return build_outcomes(metrics, window_minutes=window)


def _fetch_compose_logs(compose_argv: list[str]) -> list[str]:
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
        "--boot-only",
        action="store_true",
        help="Skip lines before last startup/pending-rescan boot marker",
    )
    return parser.parse_args(argv)


def _read_input_lines(args: argparse.Namespace) -> list[str]:
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
    args = _parse_args(argv)
    try:
        lines = _read_input_lines(args)
        outcomes = analyze_lines(
            lines,
            since_minutes=args.since_minutes,
            boot_only=args.boot_only,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1
    print(format_stdout(outcomes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
