#!/usr/bin/env python3
"""Measure listend vs sync_timedb rates from pipeline logs.

Reads pipeline container logs (stdin, --log-file, or --fetch-compose) and prints
only summary outcome lines to stdout. Errors and caveats go to stderr.

Usage (from HPCPerfStats/):
  docker compose logs --timestamps pipeline 2>&1 | python3 scripts/measure_pipeline_ingest_rate.py
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

# Docker / podman compose log prefixes (RFC3339 nano optional).
_LOG_TS_RE = re.compile(
    r"^(?P<ts>"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r")\s+\S+\s+\|\s+(?P<body>.*)$"
)
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
_THROUGHPUT_BACKLOG_RE = re.compile(
    r"Throughput telemetry: active_workers=\d+ backlog=(\d+)"
)
_BOOT_MARKERS = (
    "startup maintenance idle; ingest may begin",
    "sync_timedb: pending rescan done pending=",
)

EVEN_RATIO_TOLERANCE = 0.02
LISTEND_REPORT_WINDOW_MINUTES = 10.0


@dataclass
class LogMetrics:
    listend_unlink_sum: int = 0
    full_ingest_count: int = 0
    archive_immediate_sum: int = 0
    archive_finalize_sum: int = 0
    backlog_rescan_samples: list[tuple[datetime, int]] = field(default_factory=list)
    backlog_throughput_samples: list[tuple[datetime, int]] = field(default_factory=list)
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    timestamped_lines: int = 0


def _parse_log_timestamp(raw: str) -> Optional[datetime]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) >= 5 and text[-3] != ":" and (text[-5] in "+-") and text[-2:].isdigit():
        text = text[:-2] + ":" + text[-2:]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _strip_log_prefix(line: str) -> tuple[Optional[datetime], str]:
    match = _LOG_TS_RE.match(line.rstrip("\n"))
    if match:
        return _parse_log_timestamp(match.group("ts")), match.group("body")
    return None, line.rstrip("\n")


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
                metrics.backlog_rescan_samples.append((ts, int(rescan_match.group(1))))
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
    if metrics.backlog_rescan_samples:
        return metrics.backlog_rescan_samples[0][1]
    if metrics.backlog_throughput_samples:
        return metrics.backlog_throughput_samples[0][1]
    return None


def _backlog_latest(metrics: LogMetrics) -> Optional[int]:
    if metrics.backlog_throughput_samples:
        return metrics.backlog_throughput_samples[-1][1]
    if metrics.backlog_rescan_samples:
        return metrics.backlog_rescan_samples[-1][1]
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
    elapsed_hours = 0.0
    elapsed_minutes = 0.0
    if metrics.first_ts is not None and metrics.last_ts is not None:
        elapsed_hours = (metrics.last_ts - metrics.first_ts).total_seconds() / 3600.0
        elapsed_minutes = elapsed_hours * 60.0

    empirical_drain = 0.0
    if (
        backlog_start is not None
        and backlog_latest is not None
        and elapsed_minutes >= 1.0
    ):
        empirical_drain = (backlog_start - backlog_latest) / elapsed_minutes

    net_ingest = ingest_rate - listend_rate
    net_archive = archive_rate - listend_rate

    drained = None
    pct_complete = "N/A"
    if backlog_start is not None and backlog_latest is not None:
        drained = backlog_start - backlog_latest
        if backlog_start > 0:
            pct_complete = f"{(drained / backlog_start) * 100.0:.2f}"

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
        "empirical_drain_per_min": _fmt_rate(empirical_drain),
        "eta_hours_empirical": _eta_hours(backlog_latest, empirical_drain),
        "eta_hours_full_ingest": _eta_hours(backlog_latest, net_ingest),
        "eta_hours_archive_done": _eta_hours(backlog_latest, net_archive),
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
    if _backlog_at_start(metrics) is None:
        print("WARN: no backlog samples found; ETA fields may be N/A", file=sys.stderr)
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
        "empirical_drain_per_min",
        "eta_hours_empirical",
        "eta_hours_full_ingest",
        "eta_hours_archive_done",
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
