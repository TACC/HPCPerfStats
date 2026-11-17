"""Cross-sample timestamp cadence and monotonic E-counter checks."""
from __future__ import annotations

from collections import Counter, defaultdict

from .daemon_conf import DaemonTiming, WaitBounds, is_debug_rpm_timing, wait_bounds_from_timing
from .message_parse import schema_key_name, schema_token_is_event_counter
from .payload_parse import metric_sentinel_at_key, metric_value_at_key, rows_by_type
from .sentinel_catalog import is_bridgeable, sentinel_slug
from .shm_snapshot import SnapshotPair

# Inclusive cadence bounds tolerate float formatting / timer jitter at the edge.
_CADENCE_EPS = 1e-6

_CPU_TIME_KEYS: frozenset[str] = frozenset(
    {"user", "nice", "system", "idle", "iowait", "irq", "softirq"}
)


def _event_key_names(schema_keys: list[str]) -> list[str]:
    return [schema_key_name(k) for k in schema_keys if schema_token_is_event_counter(k)]


def _event_widths(manifest: dict, schema_by_type: dict[str, list[str]]) -> dict[tuple[str, str], int]:
    """(type, key) -> counter width from the schema ``W=`` token (64 when absent)."""
    out: dict[tuple[str, str], int] = {}
    for type_name in set(schema_by_type) | set(manifest.get("types", {})):
        info = manifest.get("types", {}).get(type_name, {})
        for tok in info.get("schema_keys") or schema_by_type.get(type_name, []):
            if schema_token_is_event_counter(tok):
                w = [p.strip()[2:] for p in tok.split(",")[1:] if p.strip().startswith("W=")]
                out[(type_name, schema_key_name(tok))] = int(w[0]) if w else 64
    return out


def _advance(va: int, vb: int, width: int) -> int:
    """Forward distance an E counter moved; negative for a real decrease.

    The daemon emits W<64 keys modulo 2^W (Grace aperf/mperf wrap 2^48 after ~26 h per core),
    so a drop whose forward distance is under half the range is a wrap, not a restart.
    """
    fwd = (vb - va) % (1 << width)
    return fwd if vb >= va or fwd < (1 << (width - 1)) else vb - va


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
                if val is None:
                    code = metric_sentinel_at_key(row.values, key_names, key)
                    val = None if code is None else -code
                if val is not None:
                    out[(type_name, row.dev, key)] = val
    return out


def _bridge_unproven(manifest: dict, type_name: str) -> bool:
    """host_cpu_hw on LIKWID without x86perf: its bridge is not proven, so a gap breaks the chain
    and a bridge violation is informational. A manifest without a slug counts as LIKWID."""
    tokens = set(str(manifest.get("capability_slug") or "likwid").split("-"))
    return type_name == "host_cpu_hw" and "likwid" in tokens and "x86perf" not in tokens


def _valid_only(event_map: dict[tuple[str, str, str], int]) -> dict[tuple[str, str, str], int]:
    """Drop sentinel entries (stored as -code) so value comparisons see readings only."""
    return {k: v for k, v in event_map.items() if v >= 0}


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
    sentinel_banner: int | None,
    warnings: list[str],
    errors: list[str],
    notes: list[str],
) -> str | None:
    def warn(msg: str) -> None:
        (errors if strict else warnings).append(f"WARN cross_sample {msg}")

    enable_slow = manifest.get("enable_slow_tier", True)
    require_tier, allowed = _pair_tier_args(pair, enable_slow)
    maps = [
        _build_event_map(
            body,
            manifest,
            schema_by_type,
            require_tier=require_tier,
            allowed_tier=allowed,
        )
        for body in (pair.body_a, *pair.bodies_mid, pair.body_b)
    ]
    widths = _event_widths(manifest, schema_by_type)
    delta = pair.ts_b - pair.ts_a
    checked = 0
    sentinels: Counter[str] = Counter()
    bridged = chain_breaks = 0
    # A counter that did not move is expected on an idle node (no I/O on a disk, no user time
    # on a core, idle GPU, unused NUMA node: c122-018 2026-11-09), so flats are a NOTE. A
    # decrease breaks the E contract and fails; liveness is the must-move canaries' job.
    flat: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for key in sorted(set().union(*maps)):
        type_name, dev, metric = key
        width = widths.get((type_name, metric), 64)
        last: int | None = None
        gap: list[int] = []
        compared = False
        for event_map in maps:
            val = event_map.get(key)
            if val is None:
                continue
            if val < 0:
                gap.append(-val)
                sentinels[sentinel_slug(-val)] += 1
                continue
            if last is not None:
                compared = True
                moved = _advance(last, val, width)
                bridge = bool(gap) and all(is_bridgeable(c) for c in gap)
                if not gap and moved < 0:
                    warn(f"{type_name}/{dev} {metric}: decreased {last} -> {val} (possible restart?)")
                elif bridge and _bridge_unproven(manifest, type_name):
                    chain_breaks += 1
                    if moved < 0:
                        notes.append(
                            f"NOTE cross_sample bridge {type_name}/{dev} {metric}: {last} -> {val}"
                            " across a gap (LIKWID host_cpu_hw bridge unproven, informational)"
                        )
                elif bridge:
                    bridged += 1
                    if moved < 0:
                        errors.append(
                            f"FAIL cross_sample bridge {type_name}/{dev} {metric}: {last} -> {val}"
                            " decreased across a bridgeable gap ("
                            + ",".join(sentinel_slug(c) for c in gap)
                            + ")"
                        )
                elif gap:
                    chain_breaks += 1
            last, gap = val, []
        checked += compared
        first, final = maps[0].get(key), maps[-1].get(key)
        if (
            first is not None
            and first == final
            and first >= 0
            and all(m.get(key, 0) >= 0 for m in maps)
            and delta >= sample_freq
        ):
            flat[(type_name, dev)].append(key)

    if sentinel_banner is None:
        if sentinels:
            errors.append(
                "FAIL cross_sample sentinel tokens without a $sentinels banner: "
                + ", ".join(f"{slug}={n}" for slug, n in sorted(sentinels.items()))
            )
        else:
            notes.append(f"SKIP cross_sample {pair.kind} sentinel bridge (emit_value_sentinels 0)")
    elif sentinels:
        notes.append(
            f"NOTE sentinel cross_sample {pair.kind}: "
            + " ".join(f"{slug}={n}" for slug, n in sorted(sentinels.items()))
            + f" (bridged {bridged}, chain-break {chain_breaks})"
        )

    valid_a = _valid_only(maps[0])
    valid_common = set(valid_a) & set(_valid_only(maps[-1]))
    flat_types: dict[str, int] = defaultdict(int)
    for (type_name, dev), keys in sorted(flat.items()):
        if (
            type_name == "host_cpu_hw"
            and not any(valid_a[k] for k in keys)
            and len(keys) == sum(1 for k in valid_common if k[:2] == (type_name, dev))
        ):
            warn(f"{type_name}/{dev}: all E-keys flat at 0 (PMC inactive or not collecting)")
            continue
        flat_types[type_name] += len(keys)
    if flat_types:
        notes.append(
            f"NOTE cross_sample flat over Δt={delta:.1f}s (expected when idle): "
            + ", ".join(f"{t} {n} keys" for t, n in sorted(flat_types.items()))
        )

    if checked:
        return f"PASS cross_sample monotonic {pair.kind} ({checked} E keys)"
    return None


def _any_advanced(
    map_a: dict[tuple[str, str, str], int],
    map_b: dict[tuple[str, str, str], int],
    widths: dict[tuple[str, str], int],
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
        if _advance(map_a[key], map_b[key], widths.get((type_name, metric), 64)) > 0:
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
        map_a.update(_valid_only(a))
        map_b.update(_valid_only(b))
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
    widths = _event_widths(manifest, schema_by_type)
    present = set(schema_by_type)

    if "host_cpu" in present:
        if _any_advanced(map_a, map_b, widths, type_names={"host_cpu"}, metrics=set(_CPU_TIME_KEYS)):
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
        if _any_advanced(map_a, map_b, widths, type_names=types):
            notes.append("PASS cross_sample canary host_vm/host_block")
        else:
            warn("canary host_vm/host_block: no E-key advanced after stimulus")

    if touched_lustre and "lustre_llite" in present:
        lustre_types = {t for t in present if t.startswith("lustre_")}
        if _any_advanced(map_a, map_b, widths, type_names=lustre_types):
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
    sentinel_banner: int | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Cadence and E-counter checks over the fast/full snapshot pairs.

    ``sentinel_banner`` is the ``$sentinels`` version from the schema (None: the daemon runs with
    emit_value_sentinels 0, so the sentinel bridge layer reports SKIP and any sentinel token
    FAILs). Chain-breaking sentinel gaps skip the monotonic check; across a bridgeable gap the
    next valid value must not be below the last one (mod 2^W), else FAIL.
    """
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
            sentinel_banner=sentinel_banner,
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
            sentinel_banner=sentinel_banner,
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


def check_sentinel_bridges(
    pair: SnapshotPair,
    manifest: dict,
    schema_by_type: dict[str, list[str]],
    *,
    sample_freq: float,
    sentinel_banner: int | None,
) -> tuple[list[str], list[str], list[str]]:
    """The monotonic / bridge-validity layer alone over one capture series (no cadence check),
    for callers that captured across a deliberate disturbance such as fault injection."""
    notes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    note = _check_monotonic_pair(
        pair,
        manifest,
        schema_by_type,
        sample_freq=sample_freq,
        strict=False,
        sentinel_banner=sentinel_banner,
        warnings=warnings,
        errors=errors,
        notes=notes,
    )
    if note:
        notes.append(note)
    return notes, warnings, errors
