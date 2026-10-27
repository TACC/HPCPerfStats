"""Parse sysstat pidstat CPU, memory, and I/O captures."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_BUSY_CPU_DEFAULT = 0.5
_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\s+(?P<ampm>AM|PM))?$",
    re.IGNORECASE,
)
_FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_NAMED_HEADER = re.compile(r"^(%?[A-Za-z][A-Za-z0-9_/%]*)$")


@dataclass
class PidstatSample:
    """One pidstat interval row (CPU and optional RSS / I/O columns)."""

    time_label: str
    pid: int | None
    cpu_pct: float | None
    usr_pct: float | None = None
    system_pct: float | None = None
    rss_kb: float | None = None
    vsz_kb: float | None = None
    mem_pct: float | None = None
    kb_rd_s: float | None = None
    kb_wr_s: float | None = None
    command: str = ""
    extras: dict[str, str] = field(default_factory=dict)


@dataclass
class PidstatSummary:
    """Aggregates used by the paper overhead and cadence sections."""

    samples: list[PidstatSample]
    average_cpu_pct: float | None
    average_usr_pct: float | None
    average_system_pct: float | None
    peak_cpu_pct: float | None
    busy_cpu_pcts: list[float]
    busy_time_labels: list[str]
    busy_spacing_seconds: list[int]
    median_busy_spacing_seconds: int | None
    peak_rss_kb: float | None
    mean_rss_kb: float | None
    peak_kb_rd_s: float | None
    peak_kb_wr_s: float | None
    mean_kb_rd_s: float | None
    mean_kb_wr_s: float | None
    interval_hint_seconds: int | None
    host_line: str = ""


def _to_float(token: str | None) -> float | None:
    if token is None or token in {"-", ""}:
        return None
    if not _FLOAT_RE.match(token):
        return None
    return float(token)


def _parse_clock(label: str) -> datetime | None:
    match = _TIME_RE.match(label.strip())
    if match is None:
        return None
    hour = int(match.group("h"))
    minute = int(match.group("m"))
    second = int(match.group("s"))
    ampm = match.group("ampm")
    if ampm:
        ampm = ampm.upper()
        if ampm == "AM":
            if hour == 12:
                hour = 0
        elif hour != 12:
            hour += 12
    return datetime(2000, 1, 1, hour, minute, second)


def _seconds_between(a: str, b: str) -> int | None:
    left = _parse_clock(a)
    right = _parse_clock(b)
    if left is None or right is None:
        return None
    delta = int((right - left).total_seconds())
    if delta < 0:
        delta += 24 * 3600
    return delta


def _split_time_prefix(tokens: list[str]) -> tuple[str | None, list[str], bool]:
    """Return (time_label, remaining_tokens, is_average)."""
    if not tokens:
        return None, tokens, False
    if tokens[0].lower().startswith("average"):
        rest = tokens[1:]
        if rest and rest[0] == ":":
            rest = rest[1:]
        return "Average", rest, True
    if _TIME_RE.match(tokens[0]):
        if len(tokens) > 1 and tokens[1].upper() in {"AM", "PM"}:
            return f"{tokens[0]} {tokens[1]}", tokens[2:], False
        return tokens[0], tokens[1:], False
    if (
        len(tokens) > 1
        and _TIME_RE.match(f"{tokens[0]} {tokens[1]}")
        and tokens[1].upper() in {"AM", "PM"}
    ):
        return f"{tokens[0]} {tokens[1]}", tokens[2:], False
    return None, tokens, False


def _is_column_header(tokens: list[str]) -> bool:
    lowered = [t.lower() for t in tokens]
    return "%cpu" in lowered and "command" in lowered


def _column_names(tokens: list[str]) -> list[str]:
    """Return pidstat field names after stripping the leading time cell(s)."""
    _label, rest, _avg = _split_time_prefix(tokens)
    names: list[str] = []
    for token in rest:
        if token.lower() in {"uid", "pid", "cpu", "command"} or _NAMED_HEADER.match(
            token
        ):
            names.append(token.lower())
        elif names:
            names.append(token.lower())
    return names


def parse_pidstat_text(text: str) -> PidstatSummary:
    """Parse a pidstat capture into samples and paper-facing aggregates.

    Accepts CPU-only ``pidstat -p PID INTERVAL COUNT`` (Stampede3 2026-08-14)
    and combined ``pidstat -h -urd -p PID …`` output.
    """
    samples: list[PidstatSample] = []
    average_cpu: float | None = None
    average_usr: float | None = None
    average_sys: float | None = None
    host_line = ""
    columns: list[str] | None = None
    interval_gaps: list[int] = []
    prev_label: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Linux "):
            host_line = line
            continue
        tokens = line.split()
        if _is_column_header(tokens):
            columns = _column_names(tokens)
            continue
        if columns is None:
            continue
        time_label, rest, is_average = _split_time_prefix(tokens)
        if time_label is None:
            continue
        row = _row_from_fields(columns, rest, time_label)
        if row is None or row.cpu_pct is None:
            continue
        if is_average:
            average_cpu = row.cpu_pct
            average_usr = row.usr_pct
            average_sys = row.system_pct
            continue
        if prev_label is not None:
            gap = _seconds_between(prev_label, row.time_label)
            if gap is not None and gap > 0:
                interval_gaps.append(gap)
        prev_label = row.time_label
        samples.append(row)

    busy = [
        s
        for s in samples
        if s.cpu_pct is not None and s.cpu_pct >= _BUSY_CPU_DEFAULT
    ]
    busy_pcts = [s.cpu_pct for s in busy if s.cpu_pct is not None]
    busy_labels = [s.time_label for s in busy]
    spacings: list[int] = []
    for left, right in zip(busy_labels, busy_labels[1:]):
        gap = _seconds_between(left, right)
        if gap is not None:
            spacings.append(gap)

    rss_values = [s.rss_kb for s in samples if s.rss_kb is not None]
    rd_values = [s.kb_rd_s for s in samples if s.kb_rd_s is not None]
    wr_values = [s.kb_wr_s for s in samples if s.kb_wr_s is not None]
    cpu_values = [s.cpu_pct for s in samples if s.cpu_pct is not None]

    return PidstatSummary(
        samples=samples,
        average_cpu_pct=average_cpu,
        average_usr_pct=average_usr,
        average_system_pct=average_sys,
        peak_cpu_pct=max(cpu_values) if cpu_values else None,
        busy_cpu_pcts=busy_pcts,
        busy_time_labels=busy_labels,
        busy_spacing_seconds=spacings,
        median_busy_spacing_seconds=_median_int(spacings),
        peak_rss_kb=max(rss_values) if rss_values else None,
        mean_rss_kb=_mean(rss_values),
        peak_kb_rd_s=max(rd_values) if rd_values else None,
        peak_kb_wr_s=max(wr_values) if wr_values else None,
        mean_kb_rd_s=_mean(rd_values),
        mean_kb_wr_s=_mean(wr_values),
        interval_hint_seconds=_median_int(interval_gaps),
        host_line=host_line,
    )


def _row_from_fields(
    columns: list[str],
    values: list[str],
    time_label: str,
) -> PidstatSample | None:
    mapped: dict[str, str] = {}
    for name, value in zip(columns, values):
        mapped[name] = value
    cpu_tok = mapped.get("%cpu")
    if cpu_tok is None:
        return None
    pid_tok = mapped.get("pid")
    return PidstatSample(
        time_label=time_label,
        pid=int(pid_tok) if pid_tok and pid_tok.isdigit() else None,
        cpu_pct=_to_float(cpu_tok),
        usr_pct=_to_float(mapped.get("%usr")),
        system_pct=_to_float(mapped.get("%system")),
        rss_kb=_to_float(mapped.get("rss")),
        vsz_kb=_to_float(mapped.get("vsz")),
        mem_pct=_to_float(mapped.get("%mem")),
        kb_rd_s=_to_float(mapped.get("kb_rd/s") or mapped.get("kbrd/s")),
        kb_wr_s=_to_float(mapped.get("kb_wr/s") or mapped.get("kbwr/s")),
        command=mapped.get("command", ""),
        extras=mapped,
    )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median_int(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))


def summary_to_dict(summary: PidstatSummary) -> dict[str, Any]:
    """Serialize a pidstat summary for JSON reports."""
    return {
        "host_line": summary.host_line,
        "n_samples": len(summary.samples),
        "interval_hint_seconds": summary.interval_hint_seconds,
        "peak_cpu_pct": summary.peak_cpu_pct,
        "average_cpu_pct": summary.average_cpu_pct,
        "average_usr_pct": summary.average_usr_pct,
        "average_system_pct": summary.average_system_pct,
        "busy_cpu_pcts": summary.busy_cpu_pcts,
        "busy_time_labels": summary.busy_time_labels,
        "busy_spacing_seconds": summary.busy_spacing_seconds,
        "median_busy_spacing_seconds": summary.median_busy_spacing_seconds,
        "peak_rss_kib": summary.peak_rss_kb,
        "mean_rss_kib": summary.mean_rss_kb,
        "peak_kb_rd_s": summary.peak_kb_rd_s,
        "peak_kb_wr_s": summary.peak_kb_wr_s,
        "mean_kb_rd_s": summary.mean_kb_rd_s,
        "mean_kb_wr_s": summary.mean_kb_wr_s,
        "missing": _missing_fields(summary),
    }


def _missing_fields(summary: PidstatSummary) -> list[str]:
    missing: list[str] = []
    if summary.peak_rss_kb is None:
        missing.append("rss")
    if summary.peak_kb_rd_s is None and summary.peak_kb_wr_s is None:
        missing.append("disk_io_rates")
    return missing
