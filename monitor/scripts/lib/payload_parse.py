"""Parse metric rows from monitor sample payloads."""
from __future__ import annotations

from dataclasses import dataclass
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


def sample_header_timestamp(body: str) -> float | None:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines or not lines[0].lstrip()[0:1].isdigit():
        return None
    from .row_validate import validate_sample_header

    ts, _, _ = validate_sample_header(lines[0].lstrip())
    return ts
