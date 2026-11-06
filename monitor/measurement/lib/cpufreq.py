"""cpufreq governor control and clock sampling for the overhead profile (D13, D25).

The same daemon costs ~8 core-s/h under ``performance`` and ~109 under ``ondemand`` on
c122-018 (finding 18): the per-hour cost is a property of the governor, while wakeups/s is
not. So the harness runs one full leg set per governor and restores every CPU's original
governor on exit.

``dynamic`` names the driver's demand-based governor (D25):

  cppc_cpufreq, acpi-cpufreq, amd-pstate (passive/guided)   ondemand
  intel_pstate, amd-pstate-epp (amd-pstate active mode)       powersave

``intel_pstate`` and ``amd-pstate-epp`` offer only ``performance`` / ``powersave``, and their
``powersave`` is the dynamic (HWP / EPP) policy. Generic-governor drivers use ``ondemand``.

Every path is relative to an injectable ``root`` (``/sys/devices/system/cpu``) so tests run on
a fake sysfs tree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

SYSFS_CPU = "/sys/devices/system/cpu"
DYNAMIC = "dynamic"
NONE = "none"
DEFAULT_GOVERNORS = "performance,dynamic"
DYNAMIC_BY_DRIVER = {
    "cppc_cpufreq": "ondemand",
    "acpi-cpufreq": "ondemand",
    "amd-pstate": "ondemand",
    "intel_pstate": "powersave",
    "amd-pstate-epp": "powersave",
}
_CPU_DIR = re.compile(r"cpu(\d+)$")
_BUSY = ("user", "nice", "system", "irq", "softirq", "steal")


class GovernorError(RuntimeError):
    """The requested governors cannot be applied on this node (raised before any change)."""


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def governor_files(root: Path | str = SYSFS_CPU) -> dict[int, Path]:
    """CPU number → its ``cpufreq/scaling_governor`` file, for every CPU that has one."""
    out: dict[int, Path] = {}
    base = Path(root)
    if not base.is_dir():
        return out
    for entry in base.iterdir():
        m = _CPU_DIR.match(entry.name)
        if m and (entry / "cpufreq" / "scaling_governor").is_file():
            out[int(m.group(1))] = entry / "cpufreq" / "scaling_governor"
    return dict(sorted(out.items()))


def read_state(root: Path | str = SYSFS_CPU) -> dict[str, Any]:
    """Driver, governors offered by every CPU, and each CPU's current governor.

    Args:
        root: ``/sys/devices/system/cpu`` or a fake tree.

    Returns:
        ``{"driver", "available", "governors": {cpu: name}}``; ``driver`` is None and
        ``governors`` empty when the kernel exposes no cpufreq policy.
    """
    files = governor_files(root)
    available: set[str] | None = None
    driver = None
    for path in files.values():
        names = set((_read(path.parent / "scaling_available_governors") or "").split())
        available = names if available is None else available & names
        driver = driver or _read(path.parent / "scaling_driver")
    return {"driver": driver, "available": sorted(available or ()),
            "governors": {cpu: _read(path) for cpu, path in files.items()}}


def parse_spec(spec: str | None) -> list[str]:
    """``--governors`` value → ordered unique names; ``none`` → [] (no governor control)."""
    names: list[str] = []
    for raw in (spec if spec is not None else DEFAULT_GOVERNORS).split(","):
        name = raw.strip().lower()
        if name and name not in names:
            names.append(name)
    if NONE in names:
        if len(names) > 1:
            raise GovernorError("--governors none cannot be combined with governor names")
        return []
    if not names:
        raise GovernorError("--governors is empty; pass names or `none`")
    return names


def resolve(names: Sequence[str], driver: str | None) -> list[str]:
    """Replace ``dynamic`` with the driver's demand-based governor (D25); keep order, dedupe."""
    out: list[str] = []
    for name in names:
        if name == DYNAMIC:
            if driver not in DYNAMIC_BY_DRIVER:
                raise GovernorError(f"no dynamic governor known for cpufreq driver {driver!r}; "
                                    "name the governor explicitly in --governors")
            name = DYNAMIC_BY_DRIVER[driver]
        if name not in out:
            out.append(name)
    return out


def plan_governors(spec: str | None, root: Path | str = SYSFS_CPU) -> dict[str, Any]:
    """Validate ``--governors`` against this node before any change.

    Args:
        spec: ``--governors`` value (None = default).
        root: cpufreq sysfs root.

    Returns:
        ``{"governors": [resolved names], "state": read_state(root)}``; ``governors`` is empty
        for ``--governors none``.

    Raises:
        GovernorError: no cpufreq policy, unknown driver for ``dynamic``, or a governor the
            driver does not offer on every CPU.
    """
    names = parse_spec(spec)
    state = read_state(root)
    if not names:
        return {"governors": [], "state": state}
    if not state["governors"]:
        raise GovernorError(f"no cpufreq scaling_governor under {root}; pass --governors none to "
                            "run without governor control (the report then names no governor)")
    resolved = resolve(names, state["driver"])
    missing = [g for g in resolved if g not in state["available"]]
    if missing:
        raise GovernorError(f"governor(s) {','.join(missing)} not offered by cpufreq driver "
                            f"{state['driver']} (scaling_available_governors: "
                            f"{' '.join(state['available']) or 'none'}); nothing was changed")
    return {"governors": resolved, "state": state}


def set_all(governor: str, root: Path | str = SYSFS_CPU) -> list[int]:
    """Write ``governor`` to every CPU's ``scaling_governor`` and read it back.

    Returns:
        The CPUs written.

    Raises:
        GovernorError: a CPU did not take the governor (write refused or read-back differs).
    """
    files = governor_files(root)
    bad = []
    for cpu, path in files.items():
        try:
            path.write_text(governor + "\n")
        except OSError as err:
            bad.append(f"cpu{cpu}: {err}")
            continue
        if _read(path) != governor:
            bad.append(f"cpu{cpu}: reads {_read(path)!r}")
    if bad:
        raise GovernorError(f"could not set governor {governor}: " + "; ".join(bad[:4]))
    return list(files)


def restore(saved: Mapping[int, str | None], root: Path | str = SYSFS_CPU) -> list[str]:
    """Write each CPU's saved governor back; returns the failures (empty when all restored)."""
    files = governor_files(root)
    failed = []
    for cpu, gov in saved.items():
        path = files.get(int(cpu))
        if path is None or not gov:
            continue
        try:
            if _read(path) != gov:
                path.write_text(gov + "\n")
        except OSError as err:
            failed.append(f"cpu{cpu}: {err}")
    return failed


def restore_lines(saved: Mapping[int, str | None], root: Path | str = SYSFS_CPU) -> list[str]:
    """Shell lines for ``restore.sh`` (one loop per original governor)."""
    by_gov: dict[str, list[int]] = {}
    for cpu, gov in saved.items():
        if gov:
            by_gov.setdefault(gov, []).append(int(cpu))
    lines = []
    for gov, cpus in sorted(by_gov.items()):
        paths = " ".join(f"{root}/cpu{c}/cpufreq/scaling_governor" for c in sorted(cpus))
        lines.append(f"for f in {paths}; do echo {gov} > \"$f\"; done")
    return lines


def cur_freq_files(root: Path | str = SYSFS_CPU) -> list[Path]:
    """Every CPU's ``cpufreq/scaling_cur_freq`` (globbed once by the sampler)."""
    return sorted(Path(root).glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))


def freq_summary_mhz(khz: Iterable[float]) -> dict[str, float] | None:
    """min / p50 / max of per-CPU clocks (kHz in, MHz out); None when nothing was readable."""
    vals = sorted(v / 1000.0 for v in khz)
    if not vals:
        return None
    mid = len(vals) // 2
    p50 = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return {"min": vals[0], "p50": p50, "max": vals[-1], "n": len(vals)}


def sample_freq(files: Iterable[Path]) -> dict[str, float] | None:
    """One clock snapshot over ``files`` (unreadable CPUs skipped)."""
    khz = []
    for path in files:
        text = _read(path)
        if text and text.isdigit():
            khz.append(int(text))
    return freq_summary_mhz(khz)


def busy_cores(cpu0: Mapping[str, int], cpu1: Mapping[str, int], seconds: float,
               clk_tck: int) -> float | None:
    """Node busy cores between two aggregate ``/proc/stat`` ``cpu`` rows (idle/iowait excluded)."""
    if seconds <= 0 or not cpu0 or not cpu1:
        return None
    return sum(cpu1.get(k, 0) - cpu0.get(k, 0) for k in _BUSY) / clk_tck / seconds
