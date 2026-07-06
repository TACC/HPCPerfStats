"""Validate metric row device IDs against manifest probes."""
from __future__ import annotations

from .payload_parse import MetricRow, iter_metric_rows


def validate_devices_in_payload(
    body: str,
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> None:
    types_info = manifest.get("types", {})
    seen: dict[str, set[str]] = {}

    for row in iter_metric_rows(
        body, schema_by_type, require_tier=require_tier, allowed_tier=allowed_tier
    ):
        info = types_info.get(row.type_name, {})
        devices = info.get("devices")
        if not devices:
            continue
        allowed = set(devices)
        if row.dev not in allowed:
            raise ValueError(
                f"type {row.type_name!r}: dev {row.dev!r} not in manifest devices {sorted(allowed)!r}"
            )
        seen.setdefault(row.type_name, set()).add(row.dev)

    for type_name, info in types_info.items():
        devices = info.get("devices")
        if not devices or type_name not in schema_by_type:
            continue
        if type_name == "host_proc":
            continue
        if type_name not in seen:
            raise ValueError(f"type {type_name!r}: no rows in payload (expected devices)")
        if type_name == "host_cpu" and len(seen[type_name]) != len(devices):
            raise ValueError(
                f"host_cpu: {len(seen[type_name])} rows, expected {len(devices)} CPU devices"
            )
        if type_name in ("host_mem", "host_numa") and len(seen[type_name]) != len(devices):
            raise ValueError(
                f"{type_name}: {len(seen[type_name])} rows, expected {len(devices)} NUMA nodes"
            )
