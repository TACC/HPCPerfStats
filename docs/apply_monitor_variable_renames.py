#!/usr/bin/env python3
"""Apply monitor variable renames from monitor_variable_rename_map.yaml.

Touches monitor/src and monitor/tests (.c/.h). Skips MSR hardware #define addresses
unless they appear as quoted emission literals.

`--write-yaml-only` rescans X()/stats_set emission tokens; tokens matching
SKIP_EVENT_TOKEN_RE / should_skip_event_token() are excluded (env vars, dlsym,
CPUID vendor strings, proc field labels with colons, etc.).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def pascal_to_snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return s.lower()


def scream_to_snake(name: str) -> str:
    return name.lower()


# Semantic overrides (plan); applied before generic lowercase.
EVENT_SEMANTIC: dict[str, str] = {
    "FIXED_CTR0": "instr_retired",
    "FIXED_CTR1": "aperf",
    "FIXED_CTR2": "mperf",
    "INST_RETIRED": "instr_retired",
    "INSTR_RETIRED_ANY": "instr_retired_any",
    "APERF": "aperf",
    "MPERF": "mperf",
    "CAS_READS": "dram_cas_reads",
    "CAS_WRITES": "dram_cas_writes",
    "ACT_COUNT": "dram_act_count",
    "PRE_COUNT_MISS": "dram_pre_count_miss",
    "FIXED_CTR": "dram_fixed_ctr",
    "FLOPS": "fp_ops_retired",
    "MERGE": "fp_ops_merge",
    "EVENT_DRAM_CHANNEL_0": "dram_chan0_bytes",
    "EVENT_DRAM_CHANNEL_1": "dram_chan1_bytes",
    "EVENT_DRAM_CHANNEL_2": "dram_chan2_bytes",
    "EVENT_DRAM_CHANNEL_3": "dram_chan3_bytes",
    "MSR_PKG_ENERGY_STATUS": "pkg_energy",
    "MSR_PP0_ENERGY_STATUS": "pp0_energy",
    "MSR_PP1_ENERGY_STATUS": "pp1_energy",
    "MSR_DRAM_ENERGY_STATUS": "dram_energy",
    "MSR_PKG_ENERGY_STAT": "pkg_energy",
    "MSR_CORE_ENERGY_STAT": "core_energy",
    "CPU_CLK_UNHALTED_CORE": "cycles_unhalted_core",
    "CPU_CLK_UNHALTED_REF": "cycles_unhalted_ref",
    "ARM_EST_FLOPS": "arm_est_flops",
    "DCGM_CPU_POWER_UTIL_W": "dcgm_cpu_power_util_w",
    "DCGM_CPU_POWER_LIMIT_W": "dcgm_cpu_power_limit_w",
    "DISPATCH_STALL_CYCLES0": "dispatch_stall_cycles0",
    "DISPATCH_STALL_CYCLES1": "dispatch_stall_cycles1",
    "BRANCH_INST_RETIRED": "branch_inst_retired",
    "BRANCH_INST_RETIRED_MISS": "branch_inst_retired_miss",
    "RETIRED_INSTRUCTIONS": "retired_instructions",
    "RETIRED_BRANCH_INSTR": "retired_branch_instr",
    "RETIRED_MISP_BRANCH_INSTR": "retired_misp_branch_instr",
}

TYPE_RENAMES: dict[str, str] = {
    "cpu": "host_cpu",
    "mem": "host_mem",
    "vm": "host_vm",
    "ps": "host_ps",
    "numa": "host_numa",
    "block": "host_block",
    "net": "host_net",
    "proc": "host_proc",
    "vfs": "host_vfs",
    "nfs": "host_nfs",
    "sysv_shm": "host_sysv_shm",
    "tmpfs": "host_tmpfs",
    "cpu_counter_metrics": "host_cpu_hw",
    "roofline_hw_peak": "host_roofline_peak",
    "intel_8pmc3": "intel_x86_pmc_gpr8",
    "intel_4pmc3": "intel_x86_pmc_gpr4",
    "amd64_pmc": "amd_x86_pmc",
    "amd64_df": "amd_x86_uncore_df",
    "amd64_rapl": "amd_x86_rapl",
    "intel_rapl": "intel_x86_rapl",
    "intel_snb_imc": "intel_x86_uncore_imc_snb",
    "intel_ivb_imc": "intel_x86_uncore_imc_ivb",
    "intel_hsw_imc": "intel_x86_uncore_imc_hsw",
    "intel_bdw_imc": "intel_x86_uncore_imc_bdw",
    "intel_skx_imc": "intel_x86_uncore_imc_skx",
    "intel_snb_cbo": "intel_x86_uncore_cbo_snb",
    "intel_ivb_cbo": "intel_x86_uncore_cbo_ivb",
    "intel_hsw_cbo": "intel_x86_uncore_cbo_hsw",
    "intel_bdw_cbo": "intel_x86_uncore_cbo_bdw",
    "intel_skx_cha": "intel_x86_uncore_cha_skx",
    "intel_snb_qpi": "intel_x86_uncore_qpi_snb",
    "intel_ivb_qpi": "intel_x86_uncore_qpi_ivb",
    "intel_hsw_qpi": "intel_x86_uncore_qpi_hsw",
    "intel_bdw_qpi": "intel_x86_uncore_qpi_bdw",
    "intel_snb_hau": "intel_x86_uncore_hau_snb",
    "intel_ivb_hau": "intel_x86_uncore_hau_ivb",
    "intel_hsw_hau": "intel_x86_uncore_hau_hsw",
    "intel_bdw_hau": "intel_x86_uncore_hau_bdw",
    "intel_snb_r2pci": "intel_x86_uncore_r2pci_snb",
    "intel_ivb_r2pci": "intel_x86_uncore_r2pci_ivb",
    "intel_hsw_r2pci": "intel_x86_uncore_r2pci_hsw",
    "intel_bdw_r2pci": "intel_x86_uncore_r2pci_bdw",
    "intel_pcu": "intel_x86_pcu",
    "arm_imc": "arm_aarch64_imc",
    "ib": "host_ib",
    "ib_ext": "host_ib_ext",
    "ib_sw": "host_ib_sw",
    "lnet": "host_lnet",
    "opa": "host_opa",
    "llite": "lustre_llite",
    "mdc": "lustre_mdc",
    "osc": "lustre_osc",
}

RETIRED_TYPES = frozenset({
    "host_mic",
    "intel_knl",
    "intel_knl_edc",
    "intel_knl_mc",
    "intel_x86_pmc_knl",
    "intel_x86_uncore_edc_knl",
    "intel_x86_uncore_mc_knl",
})

# Per-file patches after global event renames (PCU fixed counters are not core instr/aperf).
FILE_EVENT_OVERRIDES: dict[str, dict[str, str]] = {
    "intel_pcu.c": {
        "instr_retired": "pcu_ctr0",
        "aperf": "pcu_ctr1",
    },
}

# Host kernel mirror keys (also in host_key_alias; KEYS macros use new names).
HOST_MEM_EVENTS: dict[str, str] = {
    "MemTotal": "mem_total",
    "MemFree": "mem_free",
    "MemUsed": "mem_used",
    "Active": "active",
    "Inactive": "inactive",
    "Dirty": "dirty",
    "Writeback": "writeback",
    "FilePages": "file_pages",
    "Mapped": "mapped",
    "AnonPages": "anon_pages",
    "PageTables": "page_tables",
    "NFS_Unstable": "nfs_unstable",
    "Bounce": "bounce",
    "Slab": "slab",
    "AnonHugePages": "anon_huge_pages",
    "HugePages_Total": "huge_pages_total",
    "HugePages_Free": "huge_pages_free",
}

HOST_PROC_EVENTS: dict[str, str] = {
    "Uid": "uid",
    "VmPeak": "vm_peak",
    "VmSize": "vm_size",
    "VmLck": "vm_lck",
    "VmHWM": "vm_hwm",
    "VmRSS": "vm_rss",
    "VmData": "vm_data",
    "VmStk": "vm_stk",
    "VmExe": "vm_exe",
    "VmLib": "vm_lib",
    "VmPTE": "vm_pte",
    "VmSwap": "vm_swap",
    "Threads": "threads",
}

GPU_EVENTS: dict[str, str] = {
    "mem_util": "gpu_mem_util",
    "mem_total_mb": "gpu_mem_total_mb",
    "mem_used_mb": "gpu_mem_used_mb",
}

NFS_OP_EVENTS: dict[str, str] = {
    "READ_ops": "read_ops",
    "READ_timeouts": "read_timeouts",
    "READ_queue": "read_queue",
    "READ_rtt": "read_rtt",
    "WRITE_ops": "write_ops",
    "WRITE_timeouts": "write_timeouts",
    "WRITE_queue": "write_queue",
    "WRITE_rtt": "write_rtt",
}

# Tokens that must never become YAML event renames (env vars, dlsym, CPUID, format strings, …).
SKIP_EVENT_TOKEN_RE = re.compile(
    r"^(?:"
    r"HPCPERFSTATS_.*|"
    r"nvml[A-Za-z0-9_]*|"
    r"GpaInitialize|"
    r"GenuineIntel|AuthenticAMD|HygonGenuine|"
    r"PRIu\d+|PRI[sdouxX]|"
    r"TRUE|FALSE|YES|NO|NULL|ON|NONE|"
    r"Infiniband|LinkUp|Features|Bytes|"
    r".*:.*|"  # proc/sys field labels with trailing colon
    r"[A-Z]{2,}$"  # bare uppercase words (not emission keys)
    r")$"
)


def should_skip_event_token(tok: str) -> bool:
    if tok in ("cpu", "C", "Z", "mJ"):
        return True
    if tok.startswith("IA32_") or tok.startswith("MSR_"):
        return True
    return bool(SKIP_EVENT_TOKEN_RE.fullmatch(tok))


def collect_emitted_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for pat in (
        r'\bX\(([A-Za-z0-9_]+)\s*,',
        r'stats_set\([^,]+,\s*"([^"]+)"',
    ):
        for m in re.finditer(pat, text):
            tok = m.group(1)
            if not should_skip_event_token(tok):
                out.add(tok)
    return out


def build_event_map(src_dirs: list[Path]) -> dict[str, str]:
    events: dict[str, str] = {}
    events.update(EVENT_SEMANTIC)
    events.update(HOST_MEM_EVENTS)
    events.update(HOST_PROC_EVENTS)
    events.update(GPU_EVENTS)
    events.update(NFS_OP_EVENTS)

    tokens: set[str] = set()
    for d in src_dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix not in (".c", ".h"):
                continue
            tokens |= collect_emitted_tokens(p.read_text(encoding="utf-8", errors="replace"))

    for tok in sorted(tokens):
        if tok in events:
            continue
        if len(tok) <= 1:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", tok):
            events[tok] = scream_to_snake(tok)
        elif re.search(r"[A-Z]", tok) and re.search(r"[a-z]", tok):
            events[tok] = pascal_to_snake(tok)

    return events


def write_yaml(path: Path, events: dict[str, str]) -> None:
    doc = {
        "types": TYPE_RENAMES,
        "events": dict(sorted(events.items())),
        "file_event_overrides": FILE_EVENT_OVERRIDES,
        "host_mem_aliases": HOST_MEM_EVENTS,
        "host_proc_aliases": HOST_PROC_EVENTS,
        "removed_legacy": sorted(
            RETIRED_TYPES
            | {
                "CTL0", "CTL1", "CTL2", "CTL3", "CTL4", "CTL5", "CTL6", "CTL7",
                "CTR0", "CTR1", "CTR2", "CTR3", "CTR4", "CTR5", "CTR6", "CTR7",
                "FIXED_CTR", "FIXED_CTR0", "FIXED_CTR1", "FIXED_CTR2",
            }
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is None:
        raise SystemExit("PyYAML required: pip install pyyaml")
    path.write_text(
        "# Monitor variable rename map (source of truth for apply_monitor_variable_renames.py)\n"
        + yaml.dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_map(yaml_path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, str]]]:
    if yaml is None:
        raise SystemExit("PyYAML required: pip install pyyaml")
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    types = {str(k): str(v) for k, v in (doc.get("types") or {}).items()}
    events = {str(k): str(v) for k, v in (doc.get("events") or {}).items()}
    overrides = {
        str(fn): {str(k): str(v) for k, v in m.items()}
        for fn, m in (doc.get("file_event_overrides") or {}).items()
    }
    return types, events, overrides


def apply_replacements(text: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        if old == new or len(old) <= 1:
            continue
        text = text.replace(f'"{old}"', f'"{new}"')
        text = re.sub(rf"\bX\({re.escape(old)}\s*,", f"X({new},", text)
        text = re.sub(rf"\.st_name\s*=\s*\"{re.escape(old)}\"", f'.st_name = "{new}"', text)
    return text


def apply_to_tree(
    roots: list[Path],
    types: dict[str, str],
    events: dict[str, str],
    file_overrides: dict[str, dict[str, str]],
) -> list[Path]:
    event_pairs = sorted(events.items(), key=lambda kv: len(kv[0]), reverse=True)
    type_pairs = sorted(types.items(), key=lambda kv: len(kv[0]), reverse=True)
    changed: list[Path] = []

    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".c", ".h"):
                continue
            original = path.read_text(encoding="utf-8", errors="replace")
            updated = apply_replacements(original, type_pairs)
            updated = apply_replacements(updated, event_pairs)
            rel = path.name
            if rel in file_overrides:
                ov_pairs = sorted(file_overrides[rel].items(), key=lambda kv: len(kv[0]), reverse=True)
                updated = apply_replacements(updated, ov_pairs)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                changed.append(path)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--yaml",
        type=Path,
        default=Path(__file__).resolve().parent / "monitor_variable_rename_map.yaml",
    )
    ap.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Directories to rewrite (default: monitor/src monitor/tests)",
    )
    ap.add_argument("--write-yaml-only", action="store_true", help="Regenerate YAML from scan + rules")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[1]
    default_roots = [repo / "monitor" / "src", repo / "monitor" / "tests"]
    roots = args.roots if args.roots else default_roots

    if args.write_yaml_only or not args.yaml.is_file():
        events = build_event_map(roots)
        write_yaml(args.yaml, events)
        print(f"Wrote {args.yaml} ({len(TYPE_RENAMES)} types, {len(events)} events)")
        if args.write_yaml_only:
            return 0

    types, events, overrides = load_map(args.yaml)
    changed = apply_to_tree(roots, types, events, overrides)
    for p in changed:
        print(p)
    print(f"Updated {len(changed)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
