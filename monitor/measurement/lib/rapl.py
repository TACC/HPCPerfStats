"""Package/DRAM energy from powercap (Intel and AMD RAPL) or hwmon energy*_input (aarch64)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_str(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def read_energy(powercap_root: Path | str = "/sys/class/powercap",
                hwmon_root: Path | str = "/sys/class/hwmon") -> dict[str, Any]:
    """Snapshot every readable energy counter (µJ); ``source`` None when nothing is readable."""
    zones: dict[str, dict[str, Any]] = {}
    pc = Path(powercap_root)
    if pc.is_dir():
        for zdir in sorted(pc.iterdir()):
            if not zdir.name.startswith(("intel-rapl", "amd-rapl")):
                continue
            energy = _read_int(zdir / "energy_uj")
            if energy is None:
                continue
            label = _read_str(zdir / "name") or zdir.name
            zones[f"{zdir.name}/{label}"] = {
                "energy_uj": energy, "max_uj": _read_int(zdir / "max_energy_range_uj")}
    if zones:
        return {"source": "powercap", "zones": zones}
    hw = Path(hwmon_root)
    if hw.is_dir():
        for hdir in sorted(hw.iterdir()):
            for efile in sorted(hdir.glob("energy*_input")):
                energy = _read_int(efile)
                if energy is None:
                    continue
                stem = efile.name[: -len("_input")]
                label = _read_str(hdir / f"{stem}_label") or stem
                zones[f"{hdir.name}/{label}"] = {"energy_uj": energy, "max_uj": None}
    return {"source": "hwmon" if zones else None, "zones": zones}


def energy_delta_j(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float]:
    """Joules per zone, unwrapping one counter wrap via ``max_energy_range_uj``."""
    out: dict[str, float] = {}
    for name, end in after.get("zones", {}).items():
        start = before.get("zones", {}).get(name)
        if start is None:
            continue
        delta = end["energy_uj"] - start["energy_uj"]
        if delta < 0:
            if not end.get("max_uj"):
                continue
            delta += end["max_uj"]
        out[name] = delta / 1e6
    return out
