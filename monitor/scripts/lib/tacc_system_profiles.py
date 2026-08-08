"""TACC system/profile registry for shm validate (Stampede3 + Vista).

Profile names come from in-tree LSPCI fixture basenames under
tests/fixtures/{stampede3,vista}_lspci_profiles/. Never use workspace-root scrap.
"""
from __future__ import annotations

from pathlib import Path

MONITOR = Path(__file__).resolve().parents[2]
FIXTURES = MONITOR / "tests" / "fixtures"

STAMPEDE3 = "stampede3"
VISTA = "vista"

# Strict Stampede3 type contracts (plan matrix).
_STAMPEDE3_CONTRACTS: dict[str, dict[str, set[str]]] = {
    "skx": {
        "require": {"host_opa"},
        "forbid": {"nvidia_gpu", "intel_gpu", "amd_gpu"},
    },
    "clx": {
        "require": {"host_ib"},
        "forbid": {"nvidia_gpu", "intel_gpu", "amd_gpu"},
    },
    "icx": {
        "require": {"host_opa"},
        "forbid": {"nvidia_gpu", "intel_gpu", "amd_gpu"},
    },
    "spr": {
        "require": {"host_opa"},
        "forbid": {"nvidia_gpu", "intel_gpu", "amd_gpu"},
    },
    "h100": {
        "require": {"nvidia_gpu", "host_ib", "host_opa"},
        "forbid": {"intel_gpu", "amd_gpu"},
    },
    "pvc": {
        "require": {"intel_gpu", "host_opa"},
        "forbid": {"nvidia_gpu", "amd_gpu"},
    },
    "amd-rtx": {
        "require": {"nvidia_gpu", "host_ib", "host_opa", "host_cpu_hw", "amd_x86_uncore_df_turin"},
        "forbid": {"amd_gpu", "intel_gpu"},
    },
}

# Provisional Vista contracts (future live validate).
_VISTA_CONTRACTS: dict[str, dict[str, set[str]]] = {
    "gg": {
        "require": {"host_ib"},
        "forbid": {"host_opa", "intel_gpu", "amd_gpu"},
    },
    "gh": {
        "require": {"nvidia_gpu", "host_ib", "host_cpu_hw"},
        "forbid": {"host_opa", "intel_gpu", "amd_gpu"},
    },
}

_CONTRACTS_BY_SYSTEM: dict[str, dict[str, dict[str, set[str]]]] = {
    STAMPEDE3: _STAMPEDE3_CONTRACTS,
    VISTA: _VISTA_CONTRACTS,
}


def fixture_dir(system: str) -> Path:
    if system == STAMPEDE3:
        return FIXTURES / "stampede3_lspci_profiles"
    if system == VISTA:
        return FIXTURES / "vista_lspci_profiles"
    raise ValueError(f"unknown tacc system: {system!r}")


def _profile_names_from_dir(path: Path) -> frozenset[str]:
    if not path.is_dir():
        return frozenset()
    names: set[str] = set()
    for p in path.iterdir():
        if p.is_file() and p.name != "README.md" and not p.name.startswith("."):
            names.add(p.name)
    return frozenset(names)


def stampede3_profiles() -> frozenset[str]:
    names = _profile_names_from_dir(fixture_dir(STAMPEDE3))
    return names if names else frozenset(_STAMPEDE3_CONTRACTS)


def vista_profiles() -> frozenset[str]:
    names = _profile_names_from_dir(fixture_dir(VISTA))
    return names if names else frozenset(_VISTA_CONTRACTS)


def all_profiles() -> frozenset[str]:
    return stampede3_profiles() | vista_profiles()


def resolve_system(profile: str, system: str | None = None) -> str:
    """Return tacc_system for profile; raise ValueError if unknown/ambiguous."""
    s3 = stampede3_profiles()
    vi = vista_profiles()
    if system is not None:
        system = system.strip().lower()
        if system not in (STAMPEDE3, VISTA):
            raise ValueError(f"unknown --system {system!r}")
        names = s3 if system == STAMPEDE3 else vi
        if profile not in names:
            raise ValueError(f"profile {profile!r} not in system {system!r}")
        return system
    in_s3 = profile in s3
    in_vi = profile in vi
    if in_s3 and in_vi:
        raise ValueError(
            f"profile {profile!r} exists in both stampede3 and vista; pass --system"
        )
    if in_s3:
        return STAMPEDE3
    if in_vi:
        return VISTA
    raise ValueError(
        f"unknown profile {profile!r}; expected one of {sorted(all_profiles())}"
    )


def profile_artifact_suffix(slug: str, profile: str | None) -> str:
    """Filename stem suffix after capability slug (with or without profile)."""
    if profile:
        return f"{slug}__{profile}"
    return slug


def golden_basename(kind: str, slug: str, profile: str | None = None) -> str:
    """kind is schema|fast|full."""
    return f"shm_{kind}_{profile_artifact_suffix(slug, profile)}.txt"


def expectations_basename(slug: str, profile: str | None = None) -> str:
    return f"expectations_{profile_artifact_suffix(slug, profile)}.json"


def contract_for(profile: str, system: str | None = None) -> dict[str, set[str]]:
    sys_name = resolve_system(profile, system)
    table = _CONTRACTS_BY_SYSTEM[sys_name]
    if profile not in table:
        raise ValueError(f"no type contract for profile {profile!r} ({sys_name})")
    return {
        "require": set(table[profile]["require"]),
        "forbid": set(table[profile]["forbid"]),
    }


def check_profile_type_contract(
    types_present: set[str] | list[str],
    profile: str,
    *,
    system: str | None = None,
    relax: bool = False,
) -> list[str]:
    """Return error strings if schema types violate the profile contract."""
    if relax:
        return []
    present = set(types_present)
    c = contract_for(profile, system)
    errors: list[str] = []
    missing = sorted(c["require"] - present)
    if missing:
        errors.append(
            f"FAIL profile-contract {profile}: missing required types {missing}"
        )
    forbidden = sorted(c["forbid"] & present)
    if forbidden:
        errors.append(
            f"FAIL profile-contract {profile}: forbidden types present {forbidden}"
        )
    return errors


def is_stampede3_profile(profile: str) -> bool:
    return profile in stampede3_profiles()
