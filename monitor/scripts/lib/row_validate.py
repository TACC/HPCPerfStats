"""Structural validation for monitor sample rows (Python port of test_debug_shm_emit_validate)."""
from __future__ import annotations

import re

from .message_parse import fast_schema_keys, schema_key_name

_TIER_MARKERS = frozenset({"@fast", "@full"})
_DRIVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def split_fields(line: str, max_fields: int = 64) -> list[str]:
    fields: list[str] = []
    for tok in line.split():
        fields.append(tok)
        if len(fields) >= max_fields:
            break
    return fields


def st_name_looks_like_driver(type_name: str) -> bool:
    return bool(_DRIVER_NAME_RE.match(type_name))


def dev_looks_plausible(dev: str) -> bool:
    if not dev:
        return False
    return not any(ch.isspace() for ch in dev)


def token_is_uint(tok: str) -> bool:
    if not tok:
        return False
    return tok.isdigit()


def validate_sample_header(line: str) -> tuple[float, str, str]:
    fields = split_fields(line.strip(), 8)
    if len(fields) < 3:
        raise ValueError("sample header needs timestamp jobid host")
    try:
        ts = float(fields[0])
    except ValueError as exc:
        raise ValueError(f"header timestamp not numeric: {fields[0]!r}") from exc
    if not fields[1] or not fields[2]:
        raise ValueError("header jobid/host empty")
    return ts, fields[1], fields[2]


def expected_value_count(schema_keys: list[str], tier_marker: str | None) -> int:
    if tier_marker == "@fast":
        return len(fast_schema_keys(schema_keys))
    return len(schema_keys)


def validate_metric_row(
    line: str,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> tuple[str, str, str | None]:
    fields = split_fields(line.strip())
    if len(fields) < 3:
        raise ValueError(f"row too short: {line!r}")
    type_name = fields[0]
    dev = fields[1]
    if not st_name_looks_like_driver(type_name):
        raise ValueError(f"type name not driver-shaped: {type_name!r}")
    if not dev_looks_plausible(dev):
        raise ValueError(f"device not plausible for {type_name!r}: {dev!r}")
    if type_name not in schema_by_type:
        raise ValueError(f"unknown type in row: {type_name!r}")
    schema_keys = schema_by_type[type_name]
    tier_marker = None
    value_start = 2
    if fields[2] in _TIER_MARKERS:
        tier_marker = fields[2]
        value_start = 3
    if require_tier and tier_marker is None:
        raise ValueError(f"missing tier marker on row: {line!r}")
    if allowed_tier is not None and tier_marker is not None and tier_marker != allowed_tier:
        raise ValueError(f"expected tier {allowed_tier!r}, got {tier_marker!r}: {line!r}")
    values = fields[value_start:]
    expect = expected_value_count(schema_keys, tier_marker)
    if len(values) != expect:
        raise ValueError(
            f"type {type_name!r} dev {dev!r}: got {len(values)} values, expected {expect}"
        )
    for val in values:
        if not token_is_uint(val):
            raise ValueError(f"type {type_name!r} non-numeric value {val!r} in {line!r}")
    return type_name, dev, tier_marker


def validate_sample_payload(
    body: str,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> int:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty sample payload")
    validate_sample_header(lines[0])
    validated = 0
    for line in lines[1:]:
        s = line.lstrip()
        if not s or s[0] in ("%", "#", "$", "!"):
            continue
        fields = split_fields(s)
        if fields and fields[0].replace(".", "", 1).isdigit() and len(fields) >= 3:
            continue
        validate_metric_row(
            s,
            schema_by_type,
            require_tier=require_tier,
            allowed_tier=allowed_tier,
        )
        validated += 1
    if validated == 0:
        raise ValueError("no metric rows validated")
    return validated


def validate_schema_tail_rows(body: str, schema_by_type: dict[str, list[str]]) -> int:
    """Rows after $ schema block must use @full only."""
    lines = [ln for ln in body.splitlines() if ln.strip()]
    in_sample = False
    validated = 0
    for line in lines:
        s = line.lstrip()
        if not in_sample:
            if s and s[0].isdigit():
                in_sample = True
                validate_sample_header(s)
            continue
        if s[0] in ("%", "#"):
            continue
        if s[0] in ("$", "!"):
            continue
        _, _, tier = validate_metric_row(
            s,
            schema_by_type,
            require_tier=True,
            allowed_tier="@full",
        )
        if tier != "@full":
            raise ValueError(f"schema payload row must be @full: {s!r}")
        validated += 1
    return validated
