"""Collector type → driver group (decision 11: attribute cost per driver/backend, not type).

A type that touches several backends sits in the group of its most specialized backend, so
that group's cost includes the type's incidental /proc reads. The map is resolved per node
from the build's ``monitor-build-capabilities.json`` (CPU backend, IB MAD). A type in an
``Enabled types:`` line that is missing here fails preflight, and a test scans
``monitor/src`` so a new ``.st_name`` cannot land unmapped.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# aarch64 CPU backend ``arm`` (round 2, D10: /proc/stat util, cpufreq clock, LIKWID overlay;
# no DCGM) and the DCGM/NVML GPU collector are separate groups, so one SOLO leg never mixes
# their costs. ``hwmon_power`` is host_power_socket (per-socket ACPI power meters, D31): its
# own fds, independent of host_cpu_hw, so it gets its own SOLO leg.
DRIVER_GROUPS = ("filesystem", "likwid", "arm_cpu", "dcgm_gpu", "hwmon_power", "perf_event",
                 "ib_mad", "amd_gpu", "intel_gpu")
# Build-info ``cpu_backend`` values: ``arm`` from round 2 on; ``dcgm`` is the same aarch64
# backend in older RPMs (e.g. the 3.0-8 round-1 baseline, D24), so both resolve to arm_cpu.
ARM_BACKENDS = ("arm", "dcgm")

_FILESYSTEM = (
    "host_cpu", "host_mem", "host_mem_job", "host_vm", "host_numa", "host_block", "host_net",
    "host_nfs", "host_proc", "host_ps", "host_sysv_shm", "host_tmpfs", "host_vfs", "host_lnet",
    "lustre_llite", "lustre_mdc", "lustre_osc", "beegfs_client", "host_roofline_peak",
)
_LIKWID = (
    "intel_x86_rapl", "amd_x86_rapl",
    "intel_x86_uncore_imc_skx", "intel_x86_uncore_imc_icx", "intel_x86_uncore_imc_spr",
    "intel_x86_uncore_imc_emr", "intel_x86_uncore_imc_gnr", "intel_x86_uncore_imc_srf",
    "amd_x86_uncore_df_rome", "amd_x86_uncore_df_milan", "amd_x86_uncore_df_genoa",
    "amd_x86_uncore_df_turin",
)

STATIC_GROUP: dict[str, str] = {
    **dict.fromkeys(_FILESYSTEM, "filesystem"),
    **dict.fromkeys(_LIKWID, "likwid"),
    "nvidia_gpu": "dcgm_gpu",
    "host_opa": "ib_mad",
    "host_power_socket": "hwmon_power",
    "amd_gpu": "amd_gpu",
    "intel_gpu": "intel_gpu",
}
BACKEND_TYPES = ("host_cpu_hw", "host_ib", "arm_aarch64_imc")

# Paired variant legs (run like SOLO, compared against their base group in the same block,
# not against NONE). Grace overlay builds (3.0 and earlier; 3.1 removed the overlay, reads SCF
# through arm_aarch64_imc's own perf fds and gets no variant): the overlay programs the SCF
# counters inside host_cpu_hw's group whether or not arm_aarch64_imc is enabled, so SCF cost is
# only visible as arm_cpu (SCF on + arm_aarch64_imc) minus arm_cpu with
# HPCPERFSTATS_LIKWID_GRACE_SCF=0.
# ``marker`` must appear in the leg's journal or the pair is NOT_MEASURED (a daemon without
# the knob would run two identical legs and report a false zero).
VARIANTS: dict[str, dict[str, Any]] = {
    "arm_cpu_noscf": {
        "base": "arm_cpu",
        "drop": ("arm_aarch64_imc",),
        "env": {"HPCPERFSTATS_LIKWID_GRACE_SCF": "0"},
        "marker": "SCF uncore events off (HPCPERFSTATS_LIKWID_GRACE_SCF=0)",
        "label": "Grace SCF counters + arm_aarch64_imc",
    },
}
KNOWN_TYPES = tuple(sorted({*STATIC_GROUP, *BACKEND_TYPES}))

# Every build shape the map must resolve (CPU backend × IB MAD), used by the drift test.
CAPABILITY_VARIANTS = (
    {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "ib_mad_dlopen": True, "infiniband": True}},
    {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "ib_mad_dlopen": False, "infiniband": False}},
    {"arch": "aarch64", "configure": {"cpu_backend": "arm", "ib_mad_dlopen": True, "infiniband": True}},
    {"arch": "aarch64", "configure": {"cpu_backend": "arm", "likwid_overlay": True,
                                      "ib_mad_dlopen": True, "infiniband": True}},
    {"arch": "aarch64", "configure": {"cpu_backend": "arm", "ib_mad_dlopen": False, "infiniband": False}},
    {"arch": "aarch64", "configure": {"cpu_backend": "dcgm", "likwid_overlay": True,
                                      "ib_mad_dlopen": True, "infiniband": True}},
)


class UnmappedTypeError(ValueError):
    """An enabled collector type has no driver group (new collector: extend this map)."""


def _cpu_backend(caps: dict[str, Any] | None) -> str:
    cfg = (caps or {}).get("configure") or {}
    backend = (cfg.get("cpu_backend") or "").lower()
    if backend in ARM_BACKENDS:
        return "arm"
    if backend == "likwid":
        return backend
    return "arm" if (caps or {}).get("arch") == "aarch64" else "likwid"


def _ib_mad(caps: dict[str, Any] | None) -> bool:
    cfg = (caps or {}).get("configure") or {}
    if "ib_mad_dlopen" in cfg or "infiniband" in cfg:
        return bool(cfg.get("ib_mad_dlopen") or cfg.get("infiniband"))
    return True


def _likwid_overlay(caps: dict[str, Any] | None) -> bool:
    cfg = (caps or {}).get("configure") or {}
    return bool(cfg.get("likwid_overlay") or cfg.get("papi_hybrid"))


def group_for_type(name: str, caps: dict[str, Any] | None) -> str:
    """Driver group for one collector type under a given build."""
    if name == "arm_aarch64_imc":
        # Overlay builds read SCF through host_cpu_hw's LIKWID group; alone it only waits.
        return "arm_cpu" if _likwid_overlay(caps) and _cpu_backend(caps) == "arm" else "perf_event"
    if name == "host_cpu_hw":
        return "arm_cpu" if _cpu_backend(caps) == "arm" else "likwid"
    if name == "host_ib":
        return "ib_mad" if _ib_mad(caps) else "filesystem"
    try:
        return STATIC_GROUP[name]
    except KeyError:
        raise UnmappedTypeError(
            f"collector type {name!r} has no driver group; add it to lib/driver_map.py") from None


def groups_for_enabled(types: Iterable[str], caps: dict[str, Any] | None) -> dict[str, list[str]]:
    """Group the active types; raises UnmappedTypeError on any unknown type."""
    out: dict[str, list[str]] = {}
    for name in sorted(set(types)):
        out.setdefault(group_for_type(name, caps), []).append(name)
    return out


def variants_for(groups: dict[str, list[str]], caps: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Variant legs this build can run: ``{name: {base, types, env, marker, label}}``."""
    out: dict[str, dict[str, Any]] = {}
    if not _likwid_overlay(caps):
        return out
    for name, spec in VARIANTS.items():
        base = groups.get(spec["base"], [])
        if not any(t in base for t in spec["drop"]):
            continue
        types = [t for t in base if t not in spec["drop"]]
        if types:
            out[name] = {**spec, "drop": list(spec["drop"]), "types": types}
    return out


def disable_csv(kind: str, active: Iterable[str], groups: dict[str, list[str]],
                group: str | None = None) -> str | None:
    """``HPCPERFSTATS_DISABLE_TYPES`` for a leg; None means no drop-in (ALL / OFF).

    A VARIANT leg's ``group`` is a variant name; pass ``groups`` merged with the variants'
    type lists (``{**groups, name: variant["types"]}``).
    """
    active = sorted(set(active))
    if kind == "NONE":
        return ",".join(active)
    if kind in ("SOLO", "VARIANT"):
        keep = set(groups[group])
        return ",".join(t for t in active if t not in keep)
    return None
