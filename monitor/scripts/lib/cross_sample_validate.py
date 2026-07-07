"""Cross-sample timestamp cadence and monotonic E-counter checks."""
from __future__ import annotations

from .daemon_conf import DaemonTiming, WaitBounds, is_debug_rpm_timing, wait_bounds_from_timing
from .message_parse import schema_key_name, schema_token_is_event_counter
from .payload_parse import metric_value_at_key, rows_by_type
from .shm_snapshot import SnapshotPair


def _event_key_names(schema_keys: list[str]) -> list[str]:
    return [schema_key_name(k) for k in schema_keys if schema_token_is_event_counter(k)]


def _build_event_map(
    body: str,
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> dict[tuple[str, str, str], int]:
    rows = rows_by_type(
        body,
        schema_by_type,
        require_tier=require_tier,
        allowed_tier=allowed_tier,
    )
    out: dict[tuple[str, str, str], int] = {}
    for type_name, type_rows in rows.items():
        info = manifest.get("types", {}).get(type_name, {})
        schema_keys = info.get("schema_keys") or schema_by_type.get(type_name, [])
        key_names = info.get("schema_key_names") or [schema_key_name(k) for k in schema_keys]
        event_keys = _event_key_names(schema_keys)
        for row in type_rows:
            for key in event_keys:
                val = metric_value_at_key(row.values, key_names, key)
                if val is not None:
                    out[(type_name, row.dev, key)] = val
    return out


def _check_cadence(
    pair: SnapshotPair,
    bounds: WaitBounds,
    *,
    strict: bool,
    warnings: list[str],
    errors: list[str],
    notes: list[str],
) -> None:
    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN cross_sample {msg}")

    delta = pair.ts_b - pair.ts_a
    if delta <= 0:
        errors.append(
            f"FAIL cross_sample {pair.kind}_ts: did not advance ({pair.ts_a} -> {pair.ts_b})"
        )
        return

    if pair.kind == "fast":
        lo, hi = bounds.fast_cadence_min, bounds.fast_cadence_max
    else:
        lo, hi = bounds.full_cadence_min, bounds.full_cadence_max

    notes.append(f"PASS cross_sample {pair.kind}_ts Δ={delta:.1f}s")
    if delta < lo or delta > hi:
        warn(f"{pair.kind}_ts Δ={delta:.1f}s outside [{lo:.1f}, {hi:.1f}]")


def _check_monotonic_pair(
    pair: SnapshotPair,
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    sample_freq: float,
    strict: bool,
    warnings: list[str],
    errors: list[str],
) -> None:
    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN cross_sample {msg}")

    enable_slow = manifest.get("enable_slow_tier", True)
    if pair.kind == "fast" and enable_slow:
        require_tier = True
        allowed = "@fast"
    elif pair.kind == "full" and enable_slow:
        require_tier = True
        allowed = "@full"
    else:
        require_tier = False
        allowed = None

    map_a = _build_event_map(
        pair.body_a,
        manifest,
        schema_by_type,
        require_tier=require_tier,
        allowed_tier=allowed,
    )
    map_b = _build_event_map(
        pair.body_b,
        manifest,
        schema_by_type,
        require_tier=require_tier,
        allowed_tier=allowed,
    )
    delta = pair.ts_b - pair.ts_a
    common = set(map_a.keys()) & set(map_b.keys())
    checked = 0
    for key in sorted(common):
        va, vb = map_a[key], map_b[key]
        type_name, dev, metric = key
        checked += 1
        if vb < va:
            warn(
                f"{type_name}/{dev} {metric}: decreased {va} -> {vb} (possible restart?)"
            )
        elif vb == va and delta >= sample_freq:
            warn(f"{type_name}/{dev} {metric}: flat at {va} over Δt={delta:.1f}s")
    if checked:
        notes_msg = f"PASS cross_sample monotonic {pair.kind} ({checked} E keys)"
        return notes_msg
    return None


def run_cross_sample_checks(
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    timing: DaemonTiming,
    fast_pair: SnapshotPair | None,
    full_pair: SnapshotPair | None,
    strict: bool,
    active_conf_note: str,
) -> tuple[list[str], list[str], list[str]]:
    notes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    notes.append(f"PASS cross_sample conf: {active_conf_note}")
    notes.append(
        f"PASS cross_sample timing: sample_freq={timing.sample_freq} "
        f"sample_freq_slow={timing.sample_freq_slow} "
        f"enable_slow_tier={int(timing.enable_slow_tier)}"
    )
    if is_debug_rpm_timing(timing):
        notes.append("PASS cross_sample conf: debug RPM cadence (30/60)")

    bounds = wait_bounds_from_timing(timing)

    if fast_pair is not None:
        _check_cadence(fast_pair, bounds, strict=strict, warnings=warnings, errors=errors, notes=notes)
        mono_note = _check_monotonic_pair(
            fast_pair,
            manifest,
            schema_by_type,
            sample_freq=timing.sample_freq,
            strict=strict,
            warnings=warnings,
            errors=errors,
        )
        if mono_note:
            notes.append(mono_note)

    if full_pair is not None:
        _check_cadence(full_pair, bounds, strict=strict, warnings=warnings, errors=errors, notes=notes)
        freq = timing.sample_freq_slow if timing.enable_slow_tier else timing.sample_freq
        mono_note = _check_monotonic_pair(
            full_pair,
            manifest,
            schema_by_type,
            sample_freq=freq,
            strict=strict,
            warnings=warnings,
            errors=errors,
        )
        if mono_note:
            notes.append(mono_note)

    if fast_pair is None and full_pair is None:
        errors.append("FAIL cross_sample: no snapshot pairs captured")

    return notes, warnings, errors
