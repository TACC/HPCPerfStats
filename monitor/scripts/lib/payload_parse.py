"""Parse metric rows from monitor sample payloads."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .row_validate import split_fields, validate_metric_row


@dataclass(frozen=True)
class MetricRow:
    type_name: str
    dev: str
    tier: str | None
    values: list[str]


def iter_metric_rows(
    body: str,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> Iterator[MetricRow]:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return
    for line in lines[1:]:
        s = line.lstrip()
        if not s or s[0] in ("%", "#", "$", "!"):
            continue
        fields = split_fields(s)
        if fields and fields[0].replace(".", "", 1).isdigit() and len(fields) >= 3:
            continue
        type_name, dev, tier = validate_metric_row(
            s,
            schema_by_type,
            require_tier=require_tier,
            allowed_tier=allowed_tier,
        )
        value_start = 3 if tier else 2
        values = split_fields(s)[value_start:]
        yield MetricRow(type_name, dev, tier, values)


def rows_by_type(
    body: str,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> dict[str, list[MetricRow]]:
    out: dict[str, list[MetricRow]] = {}
    for row in iter_metric_rows(body, schema_by_type, require_tier=require_tier, allowed_tier=allowed_tier):
        out.setdefault(row.type_name, []).append(row)
    return out


def metric_value_at_key(
    row_values: list[str],
    key_names: list[str],
    key: str,
) -> int | None:
    try:
        idx = key_names.index(key)
    except ValueError:
        return None
    if idx >= len(row_values):
        return None
    try:
        return int(row_values[idx])
    except ValueError:
        return None


def sample_header_timestamp(body: str) -> float | None:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines or not lines[0].lstrip()[0:1].isdigit():
        return None
    from .row_validate import validate_sample_header

    ts, _, _ = validate_sample_header(lines[0].lstrip())
    return ts


def iter_row_type_dev(body: str) -> Iterator[tuple[str, str]]:
    """Lightweight type/dev scan (no schema validation) for manifest enrichment."""
    for line in body.splitlines():
        s = line.lstrip()
        if not s or s[0] in ("%", "#", "$", "!"):
            continue
        fields = split_fields(s)
        if len(fields) < 2:
            continue
        if fields[0].replace(".", "", 1).isdigit():
            continue
        yield fields[0], fields[1]


def observed_devices_from_shm(
    shm_dir: Path,
    *,
    enable_slow_tier: bool,
) -> dict[str, list[str]]:
    """Devices actually present in shm sample payloads (ground truth union)."""
    seen: dict[str, set[str]] = {}
    bodies: list[str] = []
    full = shm_dir / "full"
    if full.is_file():
        bodies.append(full.read_text(encoding="utf-8"))
    if enable_slow_tier:
        fast = shm_dir / "fast"
        if fast.is_file():
            bodies.append(fast.read_text(encoding="utf-8"))
        schema = shm_dir / "schema"
        if schema.is_file():
            bodies.append(schema.read_text(encoding="utf-8"))
    for body in bodies:
        for type_name, dev in iter_row_type_dev(body):
            seen.setdefault(type_name, set()).add(dev)
    return {k: sorted(v) for k, v in seen.items()}
