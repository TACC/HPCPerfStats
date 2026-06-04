#!/usr/bin/env python3
"""Regenerate artifacts/monitor-emitted-variables-by-architecture.md from monitor KEYS.

Run from HPCPerfStats/:
  python3 docs/regenerate_monitor_emitted_variables_by_architecture.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts" / "monitor-emitted-variables-by-architecture.md"

sys.path.insert(0, str(REPO / "docs"))
from regenerate_monitor_variable_usage_gap_analysis import collect_emitted_by_type  # noqa: E402

# (section title, type st_name, human label, optional footnote)
COMMON: list[tuple[str, str, str | None]] = [
    ("CPU / scheduler", "host_cpu", "per logical CPU", None),
    ("System", "host_ps", "device `NULL`", None),
    ("Per-process", "host_proc", "per PID", None),
    ("Memory (NUMA node)", "host_mem", "per node", None),
    ("Virtual memory", "host_vm", None, None),
    ("NUMA", "host_numa", "per node", None),
    ("Block I/O", "host_block", "per block device", None),
    ("Network", "host_net", "per interface", None),
    ("NFS", "host_nfs", "per mount", None),
    ("VFS", "host_vfs", None, None),
    ("SysV SHM", "host_sysv_shm", None, None),
    ("tmpfs", "host_tmpfs", None, None),
    ("LNet", "host_lnet", None, None),
    ("InfiniBand (sysfs port)", "host_ib", "per IB port", None),
    ("Roofline peaks", "host_roofline_peak", "host-level", None),
]

X86: list[tuple[str, str, str | None]] = [
    ("CPU counters", "host_cpu_hw", "per CPU", "LIKWID x86 path; schema includes ARM/DCGM placeholders"),
    ("Energy", "intel_x86_rapl", "per socket, Intel", None),
    ("Energy", "amd_x86_rapl", "per socket, AMD", None),
    ("AMD core PMC", "amd_x86_pmc", "per CPU", None),
    ("AMD Data Fabric", "amd_x86_uncore_df", "per CPU; Zen 17h/19h", None),
    ("Intel core PMC (4 GPR)", "intel_x86_pmc_gpr4", "per CPU", "Full schema in `intel_pmc3.h`"),
    ("Intel core PMC (8 GPR)", "intel_x86_pmc_gpr8", "per CPU", "Full schema in `intel_pmc3.h`"),
    ("Intel KNL core", "intel_x86_pmc_knl", "per CPU", None),
    ("Intel CBO SNB/IVB", "intel_x86_uncore_cbo_snb", "per core index", "Same keys as `intel_x86_uncore_cbo_ivb`"),
    ("Intel CBO SNB/IVB", "intel_x86_uncore_cbo_ivb", "per core index", None),
    ("Intel CBO HSW/BDW", "intel_x86_uncore_cbo_hsw", "per core index", "Same keys as `intel_x86_uncore_cbo_bdw`"),
    ("Intel CBO HSW/BDW", "intel_x86_uncore_cbo_bdw", "per core index", None),
    ("Intel CHA SKX", "intel_x86_uncore_cha_skx", "per core index", None),
    ("Intel IMC SNB", "intel_x86_uncore_imc_snb", "per PCI device", "Same keys as IVB/HSW/BDW IMC variants"),
    ("Intel IMC IVB", "intel_x86_uncore_imc_ivb", "per PCI device", None),
    ("Intel IMC HSW", "intel_x86_uncore_imc_hsw", "per PCI device", None),
    ("Intel IMC BDW", "intel_x86_uncore_imc_bdw", "per PCI device", None),
    ("Intel IMC SKX", "intel_x86_uncore_imc_skx", "per PCI device", None),
    ("Intel KNL MC", "intel_x86_uncore_mc_knl", "per PCI device", None),
    ("Intel KNL EDC", "intel_x86_uncore_edc_knl", "per PCI device", None),
    ("Intel QPI SNB", "intel_x86_uncore_qpi_snb", None, "Same keys across SNB/IVB/HSW/BDW QPI types"),
    ("Intel QPI IVB", "intel_x86_uncore_qpi_ivb", None, None),
    ("Intel QPI HSW", "intel_x86_uncore_qpi_hsw", None, None),
    ("Intel QPI BDW", "intel_x86_uncore_qpi_bdw", None, None),
    ("Intel HA SNB", "intel_x86_uncore_hau_snb", None, "Same keys across SNB/IVB/HSW/BDW HA types"),
    ("Intel HA IVB", "intel_x86_uncore_hau_ivb", None, None),
    ("Intel HA HSW", "intel_x86_uncore_hau_hsw", None, None),
    ("Intel HA BDW", "intel_x86_uncore_hau_bdw", None, None),
    ("Intel R2PCI SNB", "intel_x86_uncore_r2pci_snb", None, "Same keys across SNB/IVB/HSW/BDW R2PCI types"),
    ("Intel R2PCI IVB", "intel_x86_uncore_r2pci_ivb", None, None),
    ("Intel R2PCI HSW", "intel_x86_uncore_r2pci_hsw", None, None),
    ("Intel R2PCI BDW", "intel_x86_uncore_r2pci_bdw", None, None),
    ("Intel PCU", "intel_x86_pcu", "per socket; SNB–BDW", "Uses `pcu_ctr0`/`pcu_ctr1` for fixed PCU counters"),
]

ARM: list[tuple[str, str, str | None]] = [
    ("CPU counters", "host_cpu_hw", "per CPU", "DCGM backend; many x86 PMU schema slots may be zero"),
    ("ARM memory controller", "arm_aarch64_imc", "per PMU device", None),
]

OPTIONAL: list[tuple[str, str, str | None]] = [
    ("NVIDIA GPU", "nvidia_gpu", "`--enable-gpu`", None),
    ("AMD GPU", "amd_gpu", "`--enable-amd-gpu`", "Omits some NVIDIA-only keys"),
    ("InfiniBand extended", "host_ib_ext", "`--enable-infiniband`", None),
    ("IB switch 64-bit", "host_ib_sw", None, None),
    ("Intel OPA", "host_opa", "`--enable-opa`", None),
    ("Lustre MDC", "lustre_mdc", "`--enable-lustre`", None),
    ("Lustre llite", "lustre_llite", "`--enable-lustre`", None),
    ("Intel MIC", "host_mic", "`--enable-mic`", None),
]


def _fmt_keys(keys: set[str]) -> list[str]:
    return [f"- `{k}`" for k in sorted(keys)]


def _section_type(
    lines: list[str],
    label: str,
    st_name: str,
    scope: str | None,
    note: str | None,
    keys: set[str],
) -> None:
    if not keys:
        return
    scope_part = f" ({scope})" if scope else ""
    lines.append(f"### {label} — `{st_name}`{scope_part}")
    lines.append("")
    if note:
        lines.append(f"*{note}.*")
        lines.append("")
    lines.extend(_fmt_keys(keys))
    lines.append("")


def main() -> int:
    emitted = collect_emitted_by_type(include_ingest_aliases=False)
    lines: list[str] = [
        "# Monitor emitted variables (by architecture and subsystem)",
        "",
        "Inventory of variables emitted by **hpcperfstatsd** (`HPCPerfStats/monitor/`), "
        "organized by **host architecture** and **subsystem**.",
        "",
        "**Source of truth:** `KEYS` / `SCHEMA_DEF` macros in `monitor/src/` (`stats.h`), "
        "registered in `stats_registry.c`. Naming rules: "
        "`HPCPerfStats/docs/MONITOR_NAMING_SCHEME.md`.",
        "",
        f"Generated: {date.today().isoformat()} (`docs/regenerate_monitor_emitted_variables_by_architecture.py`).",
        "",
        "**Sample row format:**",
        "",
        "```text",
        "<timestamp> <jobid> <host> <stats_type> <device> <field1> <field2> ...",
        "```",
        "",
        "Schema rotation messages (`$…`) list the same field names per stats type.",
        "",
        "---",
        "",
        "## How architectures differ",
        "",
        "| Architecture | CPU counter backend | Hardware PMC/RAPL/uncore | ARM memory controller |",
        "|--------------|---------------------|--------------------------|------------------------|",
        "| **x86_64 / i?86** | LIKWID (default) | Yes (`--enable-hardware`, default on) | No |",
        "| **aarch64 / arm\\*** | DCGM (default off x86) | No Intel/AMD uncore types | Yes (`arm_aarch64_imc`) |",
        "| **ppc64 / riscv64 / other non-x86** | DCGM | No x86 hardware types | No (unless ARM host) |",
        "",
        "Optional types (GPU, extended IB, Lustre, OPA, MIC) depend on `./configure` flags "
        "and runtime hardware detection, not CPU family alone.",
        "",
        "**Runtime note:** Many Intel types are always **compiled** on x86+LIKWID builds but "
        "only **emit** when CPUID/PCI/MSR probes enable that type (`st_begin`).",
        "",
        "**Not emitted:** `lustre_osc` is implemented in `osc.c` but is **not** registered in "
        "`stats_registry.c` or `Makefile.am`.",
        "",
        "---",
        "",
        "## 1. Common — all architectures",
        "",
        "Present in every normal daemon build (`stats_registry.c`).",
        "",
    ]

    seen: set[str] = set()
    for label, st_name, scope, note in COMMON:
        keys = emitted.get(st_name, set())
        if st_name in seen:
            continue
        seen.add(st_name)
        _section_type(lines, label, st_name, scope, note, keys)

    lines.extend(["---", "", "## 2. x86_64 (LIKWID + hardware enabled)", ""])
    lines.append(
        "Requires `--with-cpu-counter-backend=likwid` (x86 default) and `--enable-hardware`."
    )
    lines.append("")
    for label, st_name, scope, note in X86:
        _section_type(lines, label, st_name, scope, note, emitted.get(st_name, set()))

    lines.extend(["---", "", "## 3. aarch64 / ARM (DCGM + hardware)", ""])
    lines.append(
        "Requires `--with-cpu-counter-backend=dcgm` (non-x86 default) and `--enable-hardware`."
    )
    lines.append("")
    for label, st_name, scope, note in ARM:
        _section_type(lines, label, st_name, scope, note, emitted.get(st_name, set()))

    lines.extend(
        [
            "---",
            "",
            "## 4. ppc64 / riscv64 / other non-x86 (DCGM, non-ARM)",
            "",
            "- **Common** types (§1)",
            "- **`host_cpu_hw`** with DCGM backend (same schema as §2/§3; no `arm_aarch64_imc` on non-ARM)",
            "- **No** LIKWID, Intel uncore, AMD PMC/RAPL/DF, or `arm_aarch64_imc`",
            "- **No** architecture-specific metric names for Power",
            "",
            "---",
            "",
            "## 5. Optional subsystems (any architecture, if built and enabled)",
            "",
        ]
    )
    for label, st_name, scope, note in OPTIONAL:
        _section_type(lines, label, st_name, scope, note, emitted.get(st_name, set()))

    common_count = sum(len(emitted.get(t, set())) for _, t, _, _ in COMMON)
    x86_only = {t for _, t, _, _ in X86} - {t for _, t, _, _ in COMMON}
    x86_count = sum(len(emitted.get(t, set())) for t in x86_only)
    opt_count = sum(len(emitted.get(t, set())) for _, t, _, _ in OPTIONAL)

    lines.extend(
        [
            "---",
            "",
            "## Summary matrix",
            "",
            "| Subsystem | Common | x86 LIKWID+HW | ARM DCGM+HW | ppc/riscv DCGM | Optional flags |",
            "|-----------|--------|---------------|-------------|----------------|----------------|",
            "| OS / proc / mem / vm / numa / block / net / nfs / vfs / shm / tmpfs / lnet / "
            "ib sysfs / roofline | ✓ | ✓ | ✓ | ✓ | — |",
            "| `host_cpu_hw` | — | ✓ | ✓ | ✓ | `--enable-hardware` |",
            "| Intel/AMD PMC, RAPL, uncore | — | ✓ | — | — | `--enable-hardware` |",
            "| `arm_aarch64_imc` | — | — | ✓ | — | ARM host + DCGM |",
            "| GPU / IB ext / Lustre / OPA / MIC | — | if configured | if configured | "
            "if configured | respective `--enable-*` |",
            "",
            f"**Approximate scale:** ~{common_count} keys in common types; x86 hardware adds "
            f"~{x86_count} more across Intel/AMD types (generation-specific; one variant active "
            f"per machine); optional subsystems add ~{opt_count} more.",
            "",
            "---",
            "",
            "## Related artifacts",
            "",
            "- `monitor-variable-rename-table.md` — old→new migration table",
            "- `monitor-variable-usage-gap-analysis.md` — downstream usage of emitted keys vs `hpcperfstats/`",
            "",
            "## Code references",
            "",
            "- `HPCPerfStats/monitor/src/stats_registry.c` — registered types",
            "- `HPCPerfStats/monitor/scripts/check_emitted_variable_names.py` — naming lint",
            "- `HPCPerfStats/hpcperfstats/listend.py` — consumer message contract",
            "",
        ]
    )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"types={len(emitted)} keys={sum(len(v) for v in emitted.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
