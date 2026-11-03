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

# DCGM is split: on Grace the CPU backend (sysmon module) and the GPU collector have very
# different costs, and one shared SOLO leg could not tell them apart.
DRIVER_GROUPS = ("filesystem", "likwid", "dcgm_cpu", "dcgm_gpu", "perf_event", "ib_mad", "amd_gpu",
                 "intel_gpu")

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
    "arm_aarch64_imc": "perf_event",
    "host_opa": "ib_mad",
    "amd_gpu": "amd_gpu",
    "intel_gpu": "intel_gpu",
}
BACKEND_TYPES = ("host_cpu_hw", "host_ib")
KNOWN_TYPES = tuple(sorted({*STATIC_GROUP, *BACKEND_TYPES}))

# Every build shape the map must resolve (CPU backend × IB MAD), used by the drift test.
CAPABILITY_VARIANTS = (
    {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "ib_mad_dlopen": True, "infiniband": True}},
    {"arch": "x86_64", "configure": {"cpu_backend": "likwid", "ib_mad_dlopen": False, "infiniband": False}},
    {"arch": "aarch64", "configure": {"cpu_backend": "dcgm", "ib_mad_dlopen": True, "infiniband": True}},
    {"arch": "aarch64", "configure": {"cpu_backend": "dcgm", "ib_mad_dlopen": False, "infiniband": False}},
)


class UnmappedTypeError(ValueError):
    """An enabled collector type has no driver group (new collector: extend this map)."""


def _cpu_backend(caps: dict[str, Any] | None) -> str:
    cfg = (caps or {}).get("configure") or {}
    backend = (cfg.get("cpu_backend") or "").lower()
    if backend in ("likwid", "dcgm"):
        return backend
    return "dcgm" if (caps or {}).get("arch") == "aarch64" else "likwid"


def _ib_mad(caps: dict[str, Any] | None) -> bool:
    cfg = (caps or {}).get("configure") or {}
    if "ib_mad_dlopen" in cfg or "infiniband" in cfg:
        return bool(cfg.get("ib_mad_dlopen") or cfg.get("infiniband"))
    return True


def group_for_type(name: str, caps: dict[str, Any] | None) -> str:
    """Driver group for one collector type under a given build."""
    if name == "host_cpu_hw":
        return "dcgm_cpu" if _cpu_backend(caps) == "dcgm" else "likwid"
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


def disable_csv(kind: str, active: Iterable[str], groups: dict[str, list[str]],
                group: str | None = None) -> str | None:
    """``HPCPERFSTATS_DISABLE_TYPES`` for a leg; None means no drop-in (ALL / OFF)."""
    active = sorted(set(active))
    if kind == "NONE":
        return ",".join(active)
    if kind == "SOLO":
        keep = set(groups[group])
        return ",".join(t for t in active if t not in keep)
    return None
