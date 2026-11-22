"""Value sentinel layers of the shm validator (tokens ``-n`` in place of a value).

Every layer reports SKIP, never PASS, on a capture without the ``$sentinels`` banner: the daemon
then runs with ``emit_value_sentinels 0`` (or predates sentinels), so a passing sentinel check
would be vacuous. A sentinel token in such a capture is a FAIL.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .host_live_probes import IB_EXT_SYSFS_FILES, probe_power_socket_watts
from .independent_read import opa_counter_files
from .payload_parse import iter_metric_rows, row_key_names
from .row_validate import token_sentinel_code
from .sentinel_catalog import (
    CATALOG_VERSION,
    SENTINEL_SLUGS,
    sentinel_slug,
    sentinels_banner_version,
)


def sentinel_keys(
    body: str,
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> dict[tuple[str, str, str], int]:
    """(type, dev, key) -> catalog code for every sentinel token in one sample payload."""
    out: dict[tuple[str, str, str], int] = {}
    for row in iter_metric_rows(
        body, schema_by_type, require_tier=require_tier, allowed_tier=allowed_tier
    ):
        for name, tok in zip(row_key_names(row, schema_by_type), row.values):
            code = token_sentinel_code(tok)
            if code is not None:
                out[(row.type_name, row.dev, name)] = code
    return out


def check_sentinel_banner(
    schema_body: str | None,
    sample_bodies: dict[str, tuple[str, bool, str | None]],
    schema_by_type: dict[str, list[str]],
) -> tuple[list[str], list[str], list[str], int | None]:
    """Banner layer: per-code NOTE counts with the banner, SKIP (or FAIL on tokens) without.

    ``sample_bodies`` maps a payload kind to (body, require_tier, allowed_tier).
    Returns notes, warnings, errors and the banner version (None without a banner).
    """
    notes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    banner = sentinels_banner_version(schema_body)
    per_kind = {
        kind: Counter(
            sentinel_slug(code)
            for code in sentinel_keys(
                body, schema_by_type, require_tier=req, allowed_tier=tier
            ).values()
        )
        for kind, (body, req, tier) in sample_bodies.items()
    }
    if banner is None:
        for kind, counts in per_kind.items():
            if counts:
                errors.append(
                    f"FAIL sentinel {kind}: sentinel tokens without a $sentinels banner: "
                    + " ".join(f"{slug}={n}" for slug, n in sorted(counts.items()))
                )
        if not errors:
            notes.append("SKIP sentinel layers (emit_value_sentinels 0: no $sentinels banner)")
        return notes, warnings, errors, None
    if banner > CATALOG_VERSION:
        warnings.append(
            f"WARN sentinel banner v{banner} is newer than the validator catalog v{CATALOG_VERSION}"
        )
    notes.append(f"PASS sentinel banner v{banner}")
    for kind, counts in sorted(per_kind.items()):
        if counts:
            notes.append(
                f"NOTE sentinel {kind}: "
                + " ".join(f"{slug}={n}" for slug, n in sorted(counts.items()))
            )
    return notes, warnings, errors, banner


def _code(slug: str) -> int:
    return next(n for n, s in SENTINEL_SLUGS.items() if s == slug)


NOT_UPDATED = _code("NOT_UPDATED")
NOT_PRESENT = _code("NOT_PRESENT")
NOT_BUILT = _code("NOT_BUILT")

# Keys of types whose static codes depend on the build; patterns ending in "*" match a prefix.
_UNCORE_NOT_BUILT_PREFIXES = ("intel_x86_uncore_imc", "amd_x86_uncore_df")
_NOT_BUILT_TYPE_PREFIXES = ("host_cpu_hw",) + _UNCORE_NOT_BUILT_PREFIXES
_INTEL_GPU_NOT_BUILT = ("gpu_pcie_rx_bytes", "gpu_pcie_tx_bytes", "gpu_xe_link_rx_bytes", "gpu_xe_link_tx_bytes")


def allowed_sentinels(slug: str, type_names) -> tuple[dict[str, dict[str, list[int]]], dict[str, list[str]]]:
    """Per type and key (``*`` = any other key; ``prefix*`` patterns), the codes this build may
    legitimately emit on a healthy node, and the keys that must always be NOT_BUILT.

    Every key may be NOT_UPDATED (transient; persistence is its own layer) and NOT_PRESENT (checked
    against the host probe where one exists). NOT_BUILT is allowed only where the build or the
    programmed eventset leaves a key unread: arm host_cpu_hw keys (dram_chan*), x86 uncore keys
    without a verified encoding, Intel GPU xpumcli link keys, IB / OPA MAD keys on a build without
    MAD, and every amd_gpu key (no reader). x86 host_cpu_hw (``x86perf``) programs every key it
    publishes, so a NOT_BUILT there fails.
    """
    tokens = set(slug.split("-"))
    nb_types = _UNCORE_NOT_BUILT_PREFIXES if "x86perf" in tokens else _NOT_BUILT_TYPE_PREFIXES
    base = [NOT_UPDATED, NOT_PRESENT]
    with_nb = base + [NOT_BUILT]
    allowed: dict[str, dict[str, list[int]]] = {}
    expect_nb: dict[str, list[str]] = {}
    for t in sorted(type_names):
        rules = {"*": base}
        if t.startswith(nb_types):
            rules["*"] = with_nb
        elif t == "intel_gpu":
            rules.update({k: with_nb for k in _INTEL_GPU_NOT_BUILT})
        elif t == "host_ib" and not tokens & {"ib", "ibdyn"}:
            rules["*"] = with_nb
            expect_nb[t] = ["sw_*"]
        elif t == "host_opa" and not tokens & {"opa", "opadyn"}:
            rules["*"] = with_nb
        elif t == "amd_gpu":
            rules["*"] = [NOT_UPDATED, NOT_BUILT]
            expect_nb[t] = ["*"]
        allowed[t] = rules
    return allowed, expect_nb


def _match(patterns, key: str):
    """The value of the most specific pattern matching ``key`` (exact, longest prefix*, then *)."""
    if key in patterns:
        return patterns[key]
    prefixes = [p for p in patterns if p.endswith("*") and p != "*" and key.startswith(p[:-1])]
    if prefixes:
        return patterns[max(prefixes, key=len)]
    return patterns.get("*")


def check_allowed_sentinels(
    manifest: dict,
    sample_bodies: dict[str, tuple[str, bool, str | None]],
    schema_by_type: dict[str, list[str]],
    *,
    strict: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Strict FAIL (WARN when not strict) on a sentinel outside the manifest allowlist, and FAIL
    when a key the build cannot read (expected NOT_BUILT) emits a value."""
    notes: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    allowed = manifest.get("allowed_sentinels")
    if allowed is None:
        notes.append("SKIP sentinel allowlist (manifest has no allowed_sentinels)")
        return notes, warnings, errors
    expect_nb = manifest.get("expect_not_built", {})
    bad = errors if strict else warnings
    for kind, (body, req, tier) in sorted(sample_bodies.items()):
        for (t, dev, key), code in sorted(
            sentinel_keys(body, schema_by_type, require_tier=req, allowed_tier=tier).items()
        ):
            codes = _match(allowed.get(t, {"*": [NOT_UPDATED, NOT_PRESENT]}), key) or []
            if code not in codes:
                bad.append(
                    f"{'FAIL' if strict else 'WARN'} sentinel allowlist {kind} {t}/{dev} {key}: "
                    f"{sentinel_slug(code)} not expected on this build"
                )
        for row in iter_metric_rows(body, schema_by_type, require_tier=req, allowed_tier=tier):
            patterns = expect_nb.get(row.type_name)
            if not patterns:
                continue
            for name, tok in zip(row_key_names(row, schema_by_type), row.values):
                if tok.isdigit() and _match({p: True for p in patterns}, name):
                    errors.append(
                        f"FAIL sentinel not_built {kind} {row.type_name}/{row.dev} {name}: value {tok}"
                        " from a key this build cannot read"
                    )
    if not errors and not warnings:
        notes.append("PASS sentinel allowlist")
    return notes, warnings, errors


def _not_present_source_exists(
    t: str, dev: str, key: str, *, vmstat: set[str], sys_root: Path, meters: set[tuple[str, str]]
) -> bool | None:
    """Whether the source of a NOT_PRESENT key exists (None: no probe for this type / device)."""
    if t == "host_vm":
        return (key in vmstat) if vmstat else None
    if t in ("host_ib", "host_opa"):
        hca, sep, port = dev.rpartition(".") if t == "host_ib" else dev.partition("/")
        port_dir = sys_root / "class" / "infiniband" / hca / "ports" / port
        if not sep or not port_dir.is_dir():
            return None
        if t == "host_opa":
            files = opa_counter_files(port_dir, key)
        else:
            files = [port_dir / d / key for d in ("counters", "hw_counters")]
            if key in IB_EXT_SYSFS_FILES:
                files.append(port_dir / "counters" / IB_EXT_SYSFS_FILES[key])
        return any(f.is_file() for f in files)
    if t == "host_power_socket":
        return (dev, key) in meters
    return None


def check_not_present_probes(
    sample_bodies: dict[str, tuple[str, bool, str | None]],
    schema_by_type: dict[str, list[str]],
    *,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
) -> tuple[list[str], list[str]]:
    """FAIL a NOT_PRESENT whose source the validator can see: a ``/proc/vmstat`` field (host_vm),
    a ``counters`` / ``hw_counters`` file (host_ib, host_opa), an ACPI hwmon meter
    (host_power_socket). nvidia_gpu has no read-only DCGM field probe: reported as SKIP."""
    notes: list[str] = []
    errors: list[str] = []
    try:
        vmstat = {ln.split()[0] for ln in (proc_root / "vmstat").read_text(encoding="utf-8").splitlines() if ln.split()}
    except OSError:
        vmstat = set()
    meters = set(probe_power_socket_watts(str(sys_root))) if "host_power_socket" in schema_by_type else set()
    checked = 0
    for kind, (body, req, tier) in sorted(sample_bodies.items()):
        for (t, dev, key), code in sorted(
            sentinel_keys(body, schema_by_type, require_tier=req, allowed_tier=tier).items()
        ):
            if code != NOT_PRESENT:
                continue
            present = _not_present_source_exists(t, dev, key, vmstat=vmstat, sys_root=sys_root, meters=meters)
            if present is None:
                continue
            checked += 1
            if present:
                errors.append(f"FAIL sentinel probe {kind} {t}/{dev} {key}: NOT_PRESENT but the source exists")
    if "nvidia_gpu" in schema_by_type:
        notes.append("SKIP sentinel probe nvidia_gpu (no DCGM field probe)")
    if checked and not errors:
        notes.append(f"PASS sentinel probe ({checked} NOT_PRESENT keys)")
    return notes, errors


def load_provenance(path: Path) -> dict[tuple[str, str, str], str]:
    """(type, dev, key) -> collector site from the DEBUG ``sentinels`` shm file (empty if absent).

    Line format: ``type dev key code slug errno site first_ts last_ts count``.
    """
    out: dict[tuple[str, str, str], str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) == 10:
            out[(parts[0], parts[1], parts[2])] = parts[6]
    return out


def check_persistent_not_updated(
    bodies: list[str],
    schema_by_type: dict[str, list[str]],
    *,
    require_tier: bool,
    allowed_tier: str | None,
    provenance: dict[tuple[str, str, str], str],
) -> list[str]:
    """FAIL every key that is NOT_UPDATED in two consecutive captures (an instrumentation gap: a
    failure path that set neither a value nor a reason). Rows of exited processes are pruned by the
    daemon, so they never reach this check."""
    errors: list[str] = []
    maps = [
        sentinel_keys(b, schema_by_type, require_tier=require_tier, allowed_tier=allowed_tier)
        for b in bodies
    ]
    flagged: set[tuple[str, str, str]] = set()
    for prev, cur in zip(maps, maps[1:]):
        for key, code in cur.items():
            if code == NOT_UPDATED and prev.get(key) == NOT_UPDATED and key not in flagged:
                flagged.add(key)
                site = provenance.get(key, "site unknown")
                errors.append(
                    f"FAIL sentinel persistent NOT_UPDATED {key[0]}/{key[1]} {key[2]}"
                    f" (collector {key[0]}, {site})"
                )
    return errors
