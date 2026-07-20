"""Cross-sample timestamp cadence and monotonic E-counter checks."""
from __future__ import annotations

from collections import defaultdict

from .daemon_conf import DaemonTiming, WaitBounds, is_debug_rpm_timing, wait_bounds_from_timing
from .message_parse import schema_key_name, schema_token_is_event_counter
from .payload_parse import metric_value_at_key, rows_by_type
from .shm_snapshot import SnapshotPair

# Inclusive cadence bounds tolerate float formatting / timer jitter at the edge.
_CADENCE_EPS = 1e-6

# Gauges / watermarks that may legitimately stay constant at non-zero.
_ALLOW_FLAT: frozenset[tuple[str, str]] = frozenset(
    {
        ("host_lnet", "msgs_alloc"),
        ("host_lnet", "msgs_alloc_max"),
    }
)

_CPU_TIME_KEYS: frozenset[str] = frozenset(
    {"user", "nice", "system", "idle", "iowait", "irq", "softirq"}
)


def allow_flat_zero(type_name: str, metric: str) -> bool:
    """True when flat-at-0 is expected for sparse/error/idle-opcode counters."""
    m = metric.lower()
    if m == "errors" or m.endswith("_error") or m.endswith("_errors") or m.endswith("_err"):
        return True
    if "error" in m:
        return True
    if type_name.startswith("lustre_") and (
        m.startswith("vfs_") or m.startswith("mdc_") or m.startswith("osc_")
    ):
        return True
    if type_name in ("nvidia_gpu", "amd_gpu", "intel_gpu", "dcgm_gpu") and (
        "ecc" in m or "xid" in m or m.endswith("_drop") or "throttle" in m
    ):
        return True
    return False


def allow_flat(type_name: str, metric: str) -> bool:
    return (type_name, metric) in _ALLOW_FLAT


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
    if delta < lo - _CADENCE_EPS or delta > hi + _CADENCE_EPS:
        warn(f"{pair.kind}_ts Δ={delta:.1f}s outside [{lo:.1f}, {hi:.1f}]")


def _pair_tier_args(
    pair: SnapshotPair, enable_slow: bool
) -> tuple[bool, str | None]:
    if pair.kind == "fast" and enable_slow:
        return True, "@fast"
    if pair.kind == "full" and enable_slow:
        return True, "@full"
    return False, None


def _check_monotonic_pair(
    pair: SnapshotPair,
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    sample_freq: float,
    strict: bool,
    warnings: list[str],
    errors: list[str],
    notes: list[str],
) -> str | None:
    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN cross_sample {msg}")

    enable_slow = manifest.get("enable_slow_tier", True)
    require_tier, allowed = _pair_tier_args(pair, enable_slow)

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
    silenced_zero = 0
    silenced_types: set[str] = set()
    # Defer host_cpu_hw flats for per-device aggregation.
    hw_keys: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)

    for key in sorted(common):
        va, vb = map_a[key], map_b[key]
        type_name, dev, metric = key
        checked += 1
        if vb < va:
            warn(
                f"{type_name}/{dev} {metric}: decreased {va} -> {vb} (possible restart?)"
            )
            continue
        if vb != va or delta < sample_freq:
            continue
        # Flat over a full sample interval.
        if type_name == "host_cpu_hw":
            hw_keys[(type_name, dev)].append(key)
            continue
        if allow_flat(type_name, metric):
            continue
        if va == 0 and allow_flat_zero(type_name, metric):
            silenced_zero += 1
            silenced_types.add(type_name)
            continue
        warn(f"{type_name}/{dev} {metric}: flat at {va} over Δt={delta:.1f}s")

    for (type_name, dev), keys in sorted(hw_keys.items()):
        # All E-keys present for this device in the pair (not only deferred flats).
        all_dev_keys = [
            k for k in common if k[0] == type_name and k[1] == dev
        ]
        all_flat = all(map_b[k] == map_a[k] for k in all_dev_keys)
        all_zero = all(map_a[k] == 0 for k in all_dev_keys)
        if all_dev_keys and all_flat and all_zero:
            warn(
                f"{type_name}/{dev}: all E-keys flat at 0 "
                f"(PMC inactive or not collecting)"
            )
            continue
        for key in keys:
            va = map_a[key]
            if va == 0:
                continue  # partial zero flats: silence; non-zero stuck still warn
            _t, _d, metric = key
            warn(f"{type_name}/{dev} {metric}: flat at {va} over Δt={delta:.1f}s")

    if silenced_zero:
        notes.append(
            "NOTE cross_sample allow-flat-zero: silenced "
            f"{silenced_zero} flat-at-0 keys "
            f"({', '.join(sorted(silenced_types))})"
        )

    if checked:
        return f"PASS cross_sample monotonic {pair.kind} ({checked} E keys)"
    return None


def _any_advanced(
    map_a: dict[tuple[str, str, str], int],
    map_b: dict[tuple[str, str, str], int],
    *,
    type_names: set[str] | None = None,
    metrics: set[str] | None = None,
) -> bool:
    common = set(map_a.keys()) & set(map_b.keys())
    for key in common:
        type_name, _dev, metric = key
        if type_names is not None and type_name not in type_names:
            continue
        if metrics is not None and metric not in metrics:
            continue
        if map_b[key] > map_a[key]:
            return True
    return False


def _merge_event_maps(
    pairs: list[SnapshotPair],
    manifest: dict,
    schema_by_type: dict[str, list[str]],
) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str, str], int]]:
    """Union event maps across pairs (prefer full when both exist)."""
    enable_slow = manifest.get("enable_slow_tier", True)
    map_a: dict[tuple[str, str, str], int] = {}
    map_b: dict[tuple[str, str, str], int] = {}
    for pair in pairs:
        require_tier, allowed = _pair_tier_args(pair, enable_slow)
        a = _build_event_map(
            pair.body_a,
            manifest,
            schema_by_type,
            require_tier=require_tier,
            allowed_tier=allowed,
        )
        b = _build_event_map(
            pair.body_b,
            manifest,
            schema_by_type,
            require_tier=require_tier,
            allowed_tier=allowed,
        )
        map_a.update(a)
        map_b.update(b)
    return map_a, map_b


def _check_must_move_canaries(
    pairs: list[SnapshotPair],
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    touched_lustre: bool,
    strict: bool,
    warnings: list[str],
    errors: list[str],
    notes: list[str],
) -> None:
    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN cross_sample {msg}")

    if not pairs:
        return

    map_a, map_b = _merge_event_maps(pairs, manifest, schema_by_type)
    present = set(schema_by_type)

    if "host_cpu" in present:
        if _any_advanced(map_a, map_b, type_names={"host_cpu"}, metrics=set(_CPU_TIME_KEYS)):
            notes.append("PASS cross_sample canary host_cpu")
        else:
            warn("canary host_cpu: no E-key advanced after stimulus")

    has_vm = "host_vm" in present
    has_block = "host_block" in present
    if has_vm or has_block:
        types: set[str] = set()
        if has_vm:
            types.add("host_vm")
        if has_block:
            types.add("host_block")
        if _any_advanced(map_a, map_b, type_names=types):
            notes.append("PASS cross_sample canary host_vm/host_block")
        else:
            warn("canary host_vm/host_block: no E-key advanced after stimulus")

    if touched_lustre and "lustre_llite" in present:
        lustre_types = {t for t in present if t.startswith("lustre_")}
        if _any_advanced(map_a, map_b, type_names=lustre_types):
            notes.append("PASS cross_sample canary lustre")
        else:
            warn("canary lustre: no E-key advanced after stimulus")


def run_cross_sample_checks(
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    timing: DaemonTiming,
    fast_pair: SnapshotPair | None,
    full_pair: SnapshotPair | None,
    strict: bool,
    active_conf_note: str,
    check_canaries: bool = False,
    touched_lustre: bool = False,
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
            notes=notes,
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
            notes=notes,
        )
        if mono_note:
            notes.append(mono_note)

    if fast_pair is None and full_pair is None:
        errors.append("FAIL cross_sample: no snapshot pairs captured")

    if check_canaries:
        pairs = [p for p in (fast_pair, full_pair) if p is not None]
        _check_must_move_canaries(
            pairs,
            manifest,
            schema_by_type,
            touched_lustre=touched_lustre,
            strict=strict,
            warnings=warnings,
            errors=errors,
            notes=notes,
        )

    return notes, warnings, errors
