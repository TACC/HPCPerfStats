#!/usr/bin/env python3
"""Rebuild docs/MONITOR_VARIABLES.md from variableMetadataMonitorEvents.js + repo reference scan.

Run from repo root (HPCPerfStats/) with .venv:
  .venv/bin/python docs/regenerate_monitor_variables_catalog.py
"""
from __future__ import annotations

import codecs
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = Path(__file__).resolve().parent / "MONITOR_VARIABLES.md"
JS = REPO / "hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEvents.js"

sys.path.insert(0, str(REPO))


def _parse_js() -> tuple[dict[str, str], list[str]]:
    text = JS.read_text(encoding="utf-8")
    pat = re.compile(
        r"^\s+([A-Za-z0-9_]+):\s*\{\s*description:\s*\"((?:[^\"\\]|\\.)*)\"\s*\}",
        re.M,
    )

    def dec(s: str) -> str:
        return codecs.decode(s, "unicode_escape")

    items = [(k, dec(d)) for k, d in pat.findall(text)]
    items.sort(key=lambda x: x[0])
    return dict(items), [k for k, _ in items]


def _event_to_types():
    from hpcperfstats.dbload.sync_timedb_parsing import EVENTMAPS_BY_TYPE

    event_to_types: dict[str, set[str]] = defaultdict(set)
    for typ, em in EVENTMAPS_BY_TYPE.items():
        for v in em.values():
            ev = v.split(",")[0].strip()
            event_to_types[ev].add(typ)
    manual = {
        "user": {"cpu"},
        "nice": {"cpu"},
        "system": {"cpu"},
        "idle": {"cpu"},
        "iowait": {"cpu"},
        "irq": {"cpu"},
        "softirq": {"cpu"},
        "rd_sectors": {"block"},
        "wr_sectors": {"block"},
        "rx_bytes": {"net", "lnet"},
        "tx_bytes": {"net", "lnet"},
        "MemUsed": {"mem"},
        "MemTotal": {"mem"},
        "gpu_util": {"nvidia_gpu", "amd_gpu"},
        "tensor_active": {"nvidia_gpu", "amd_gpu"},
        "read_bytes": {"llite"},
        "write_bytes": {"llite"},
        "port_rcv_data": {"ib_ext"},
        "port_xmit_data": {"ib_ext"},
        "normal_read": {"nfs"},
        "READ_ops": {"nfs"},
        "VmHWM": {"proc"},
        "VmRSS": {"proc"},
        "Threads": {"proc"},
        "load_1": {"ps"},
        "load_5": {"ps"},
        "load_15": {"ps"},
        "MSR_PKG_ENERGY_STATUS": {"intel_rapl"},
        "MSR_PKG_ENERGY_STAT": {"amd64_rapl"},
    }
    for k, ts in manual.items():
        event_to_types[k] |= ts
    return event_to_types


def _scan_refs(keys: list[str]) -> dict[str, list[str]]:
    roots = [
        REPO / "hpcperfstats/dbload",
        REPO / "hpcperfstats/analysis",
        REPO / "hpcperfstats/site/machine",
        REPO / "hpcperfstats/site/frontend/src",
    ]
    files: list[Path] = []
    for r in roots:
        if not r.exists():
            continue
        for p in r.rglob("*"):
            if p.suffix not in (".py", ".js", ".jsx"):
                continue
            if "node_modules" in p.parts or "__pycache__" in p.parts:
                continue
            files.append(p)
    contents: dict[Path, str] = {}
    for p in files:
        try:
            contents[p] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    refs = {k: [] for k in keys}
    for path, text in contents.items():
        r = str(path.relative_to(REPO))
        for k in keys:
            if f'"{k}"' in text or f"'{k}'" in text:
                refs[k].append(r)
    # normalize to hpcperfstats/ prefix
    out = {}
    for k in keys:
        out[k] = sorted(set(refs[k]))
    return out


SKIP_IN_REFS = frozenset(
    {
        "hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEvents.js",
        "hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py",
    }
)


def _split_refs(refs: list[str]) -> tuple[list[str], list[str]]:
    raw = [r for r in refs if r not in SKIP_IN_REFS]
    tests = [r for r in raw if "/tests/" in r or r.endswith(".test.js")]
    app = [r for r in raw if r not in tests]
    return app, tests


def _domain(k: str) -> str:
    if k.startswith("MBW_CHANNEL_") or k in (
        "CAS_READS",
        "CAS_WRITES",
        "DF_CTR0",
        "DF_CTR1",
        "DF_CTR2",
        "DF_CTR3",
    ):
        return "DRAM / memory controller"
    if k.startswith("FP_ARITH") or k in (
        "FLOPS",
        "MERGE",
        "INST_RETIRED",
        "BRANCH_INST_RETIRED",
        "BRANCH_INST_RETIRED_MISS",
        "DISPATCH_STALL_CYCLES0",
        "DISPATCH_STALL_CYCLES1",
        "SSE_DOUBLE_SCALAR",
        "SSE_DOUBLE_PACKED",
        "SIMD_DOUBLE_256",
        "ARM_EST_FLOPS",
    ):
        return "CPU core performance (PMC)"
    if k in ("APERF", "MPERF", "FIXED_CTR0", "FIXED_CTR1", "FIXED_CTR2", "FIXED_CTR"):
        return "CPU cycles / frequency"
    if re.fullmatch(r"CTL\d+", k) or re.fullmatch(r"CTR\d+", k) or re.match(r"V[13]_CTL\d+", k) or re.match(r"V[13]_CTR\d+", k):
        return "PMC schema / programming (decoded before DB)"
    if k.startswith("gpu_") or k in (
        "tensor_active",
        "mem_util",
        "power_usage",
        "sysio_power_usage",
        "module_power_usage",
        "temperature",
        "fp64_active",
        "fp32_active",
        "fp16_active",
        "sm_active",
        "sm_occupancy",
        "clocks_event_reasons",
        "mem_total_mb",
        "mem_used_mb",
        "gpu_count",
    ):
        return "GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)"
    if k.startswith("port_") or k.startswith("Port") or k in ("symbol_error", "VL15_dropped", "counter_select", "port_select"):
        return "InfiniBand / Omni-Path / HFI"
    if k in (
        "rx_bytes",
        "tx_bytes",
        "rx_packets",
        "tx_packets",
        "rx_errors",
        "tx_errors",
        "rx_dropped",
        "tx_dropped",
        "collisions",
        "multicast",
    ):
        return "Ethernet (per-interface); LNET may reuse byte keys"
    if k.startswith("mds_") or k.startswith("ost_") or k.startswith("ldlm_") or k in (
        "open",
        "close",
        "getattr",
        "mmap",
        "fsync",
        "setattr",
        "truncate",
        "flock",
        "statfs",
        "alloc_inode",
        "setxattr",
        "listxattr",
        "removexattr",
        "readdir",
        "create",
        "lookup",
        "link",
        "unlink",
        "symlink",
        "mkdir",
        "rmdir",
        "mknod",
        "rename",
    ):
        return "Lustre client (llite / mdc / osc)"
    if k in (
        "delay",
        "normal_read",
        "normal_write",
        "direct_read",
        "direct_write",
        "server_read",
        "server_write",
    ) or k.startswith("xprt_") or k.startswith("READ_") or k.startswith("WRITE_"):
        return "NFS client (mountstats)"
    if k.startswith("msgs_") or k in (
        "errors",
        "tx_msgs",
        "rx_msgs",
        "route_msgs",
        "rx_msgs_dropped",
        "route_bytes",
        "rx_bytes_dropped",
    ):
        return "Lustre LNET"
    if k.startswith("numa_") or k in ("interleave_hit", "local_node", "other_node"):
        return "NUMA statistics"
    if k.startswith("pg") or k in (
        "pswpin",
        "pswpout",
        "allocstall",
        "pageoutrun",
        "slabs_scanned",
        "kswapd_steal",
        "kswapd_inodesteal",
        "nr_anon_transparent_hugepages",
    ):
        return "Kernel VM (/proc/vmstat)"
    if k in (
        "MemTotal",
        "MemFree",
        "MemUsed",
        "Active",
        "Inactive",
        "Dirty",
        "Writeback",
        "Slab",
        "Mapped",
        "AnonPages",
        "PageTables",
        "HugePages_Total",
        "HugePages_Free",
        "AnonHugePages",
        "NFS_Unstable",
        "Bounce",
        "FilePages",
    ):
        return "NUMA node memory (meminfo fields)"
    if k.startswith("MSR_") or k.startswith("MSR"):
        return "RAPL / energy MSRs"
    if k in (
        "user",
        "nice",
        "system",
        "idle",
        "iowait",
        "irq",
        "softirq",
        "ctxt",
        "processes",
        "nr_running",
        "nr_threads",
    ):
        return "Host CPU time (/proc/stat style)"
    if k.startswith("rd_") or k.startswith("wr_") or k in ("io_ticks", "time_in_queue", "in_flight"):
        return "Block device I/O"
    if k.startswith("Vm") or k in ("Threads", "Uid"):
        return "Sampled process /proc status"
    if k.startswith("load_"):
        return "Load average (ps stats type)"
    if k in (
        "user_sum",
        "nice_sum",
        "sys_sum",
        "idle_sum",
        "jiffy_counter",
        "num_cores",
        "threads_core",
    ):
        return "Intel Xeon Phi (MIC)"
    if k in ("dentry_use", "file_use", "inode_use"):
        return "VFS"
    if k in ("bytes_used", "files_used"):
        return "tmpfs"
    if k in ("mem_used", "segs_used"):
        return "SysV shared memory"
    if k in ("DCGM_CPU_POWER_UTIL_W", "DCGM_CPU_POWER_LIMIT_W", "ARM_DRAM_BW_BYTES"):
        return "cpu_counter_metrics (Grace / DCGM / synthetic)"
    if k == "ARM_EST_FLOPS":
        return "ARM / DCGM synthetic counters"
    return "General / multi-type (see monitor `host_data.type`)"


def _fmt_types(event_to_types: dict[str, set[str]], k: str) -> str:
    ts = event_to_types.get(k)
    if not ts:
        return "*(infer from job schema / monitor enablement)*"
    return ", ".join(f"`{t}`" for t in sorted(ts))


def _section_for_key(
    k: str,
    desc: str,
    event_to_types: dict[str, set[str]],
    app: list[str],
    tests: list[str],
) -> str:
    lines = [f"### `{k}`", ""]
    lines.append(f"- **Definition:** {desc}")
    lines.append(f"- **Domain:** {_domain(k)}")
    lines.append(f"- **Typical `host_data.type` values:** {_fmt_types(event_to_types, k)}")
    if re.fullmatch(r"CTL\d+", k) or re.fullmatch(r"CTR\d+", k) or re.match(r"V[13]_CTL", k) or re.match(r"V[13]_CTR", k):
        lines.append(
            "- **Additional references:** *(schema placeholders; logical event names are persisted after "
            "`map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*"
        )
        if app or tests:
            lines.append(f"  - String matches also in: {'; '.join(app + tests)}")
    elif not app and not tests:
        lines.append(
            "- **Additional references:** *(none outside universal ingest / schema — may still appear in "
            "type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*"
        )
    else:
        if app:
            lines.append(f"- **Application / library code:** {'; '.join(app)}")
        if tests:
            lines.append(f"- **Tests:** {'; '.join(tests)}")
    # Blank line before the next ### heading (join(["x",""]) yields only one trailing \n).
    return "\n".join(lines) + "\n\n"


def main() -> int:
    if not JS.is_file():
        print("Missing", JS, file=sys.stderr)
        return 1
    desc_map, keys = _parse_js()
    event_to_types = _event_to_types()
    refs = _scan_refs(keys)

    header = """# Monitor-originated telemetry variables

This document catalogs **`host_data.event` names** that the HPCPerfStats monitor can publish (aligned with `HPCPerfStats/monitor/src` `KEYS` macros and the generator in `hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py`).

**Regenerating definitions:** run `python3 hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py` to refresh `variableMetadataMonitorEvents.js`.

**Diagnostic bullets** (for events not wired into job metrics/plots) are added by `docs/augment_monitor_variables_diagnostics.py` after this catalog is regenerated.

---

## End-to-end data path

1. **Monitor** (C sources under `HPCPerfStats/monitor/`) samples counters and prints text lines (`t jid host` timestamps plus `type dev values` rows and `!type` schema lines).
2. **`hpcperfstats/listend.py`** (`on_message`) appends payloads under the per-host archive directory (RabbitMQ consumer).
3. **`hpcperfstats/dbload/sync_timedb.py`** (`add_stats_file_to_db`) reads archive files.
4. **`hpcperfstats/dbload/sync_timedb_parsing.py`** (`parse_stats_lines`, `compute_deltas_and_arc`, `EVENTMAPS_BY_TYPE`) parses lines, maps raw PMC encodings to logical event names, collapses multi-GPU rows, and computes `delta` / `arc`.
5. **`hpcperfstats/dbload/io_helpers.py`** (`host_data_instance_from_stats_row`) builds ORM rows.
6. **`hpcperfstats/site/machine/models.py`** (`host_data` model) stores `time`, `host`, `type`, `dev`, `event`, `unit`, `value`, `delta`, `arc`.
7. **Analysis** (`jid_table`, `metrics`, `plot/*`) and **API/SPA** query `host_data` by job window and schema.

---

## Classifications

### By lifecycle stage

| Stage | Role | Primary modules |
|-------|------|-----------------|
| Transport / archive | Receive monitor payloads, write files | `listend.py` |
| Parse & normalize | Decode PMC schema, units, deltas, GPU collapse | `dbload/sync_timedb_parsing.py` |
| Load into DB | Batch insert `host_data` | `dbload/sync_timedb.py` |
| Job window & schema | Distinct `(type, event)` for a job’s hosts/times | `analysis/gen/jid_table.py`, `TypeDetailDataProvider`, `HostDataProvider` |
| Job metrics | Aggregate to `metrics_data` (avg/max/imbalance, etc.) | `analysis/metrics/metrics.py` |
| Summary plots | Time-series subplots per job | `analysis/plot/summaryplot.py`, `summary_metric_descriptions.py` |
| Roofline | DRAM CAS + FLOPs for arithmetic intensity | `analysis/plot/roofline.py`, `roofline_peaks.py` |
| Node power estimate | Combine RAPL / DCGM CPU / GPU power fields | `analysis/gen/node_power_est.py` |
| API & type detail | JSON for job/host/type explorers | `site/machine/api.py` |
| UI tooltips | Human-readable event text | `site/frontend/src/utils/variableMetadata.js` (`getDescriptionForVariable`), `variableMetadataMonitorEvents.js` |

### By monitor `host_data.type` (`stats_type.st_name`)

| `host_data.type` | Monitor source (`.c`) | Default ingest notes |
|------------------|----------------------|----------------------|
| `amd64_df` | `amd64_df.c` | HW map → `MBW_CHANNEL_*` |
| `amd_gpu` | `amd_gpu.c` | Same KEY names as NVIDIA where applicable (`st_name` is `amd_gpu`, not `amd64_gpu`) |
| `amd64_pmc` | `amd64_pmc.c` | HW map → `FLOPS`, branch/stall events |
| `amd64_rapl` | `amd64_rapl.c` | Package energy |
| `arm_imc` | `arm_imc.c` | `CAS_READS` / `CAS_WRITES` |
| `block` | `block.c` | Block sysfs counters |
| `cpu` | `cpu.c` | Per-CPU jiffies |
| `cpu_counter_metrics` | `cpu_counter_metrics.c` | Intel/AMD/ARM paths; DCGM CPU power; synthetic ARM metrics |
| `ib` | `ib.c` | Skipped by default ingest (`exclude_types`) |
| `ib_ext` | `ib_ext.c` | Extended IB counters |
| `ib_sw` | `ib_sw.c` | Skipped by default ingest |
| `intel_4pmc3` | `intel_4pmc3.c` | Same decode map as `intel_8pmc3` |
| `intel_8pmc3` | `intel_8pmc3.c` | FP_ARITH / fixed counters / legacy SSE FLOP proxies |
| `intel_*_imc` | `intel_*_imc.c` | IMC generations → `CAS_READS` / `CAS_WRITES` |
| `intel_knl_mc_dclk` | `intel_knl_mc.c + dbload normalization` | KNL DRAM CAS |
| `intel_skx_cha` | `intel_skx_cha.c` | CHA uncore events (summary arc sum) |
| `intel_rapl` | `intel_rapl.c` | RAPL MSRs |
| `intel_pcu` | `intel_pcu.c` | Package control / uncore |
| `intel_*_cbo`, `intel_*_qpi`, `intel_*_hau`, `intel_*_r2pci` | various `intel_*.c` | Platform uncore (usage varies) |
| `llite` | `llite.c` | Lustre client |
| `lnet` | `lnet.c` | LNET counters |
| `mdc` | `mdc.c` | Lustre MDC stats |
| `mem` | `mem.c` | System memory |
| `mic` | `mic.c` | Xeon Phi aggregate CPU |
| `net` | `net.c` | Ethernet sysfs |
| `nfs` | `nfs.c` | NFS mountstats |
| `numa` | `numa.c` | NUMA hit/miss |
| `nvidia_gpu` | `nvidia_gpu.c` | DCGM GPU metrics |
| `opa` | `opa.c` | Omni-Path |
| `osc` | `osc.c` | Lustre OSC |
| `proc` | `proc.c` | Per-process `/proc` status |
| `ps` | `ps.c` | Skipped by default ingest |
| `sysv_shm` | `sysv_shm.c` | Skipped by default ingest |
| `tmpfs` | `tmpfs.c` | Skipped by default ingest |
| `vfs` | `vfs.c` | Skipped by default ingest |
| `vm` | `vm.c` | VM stats |

Exact `st_name` values are in `HPCPerfStats/monitor/src/*.c` (grep `.st_name`). Some typenames are normalized during dbload (for example KNL memory controller).

### By functional domain (summary)

- **CPU time & load:** `cpu`, `ps`, `mic` types — `user`, `system`, `load_*`, …
- **Core PMC / FLOPs / frequency:** `intel_*pmc3`, `amd64_pmc`, `cpu_counter_metrics` — `FP_ARITH_*`, `FLOPS`, `INST_RETIRED`, `APERF`, `MPERF`, …
- **DRAM bandwidth:** `intel_*_imc`, `intel_knl_mc_dclk`, `arm_imc`, `amd64_df` — `CAS_READS` / `CAS_WRITES`, `MBW_CHANNEL_*`
- **GPU:** `nvidia_gpu`, `amd_gpu` — `gpu_util`, `tensor_active`, `power_usage`, …
- **High-speed fabric:** `ib_ext`, `opa` — `port_*`, `Port*` counters
- **Ethernet / LNET:** `net`, `lnet` — `rx_bytes`, `tx_bytes`, …
- **Local disk:** `block` — `rd_sectors`, `wr_sectors`, …
- **Shared filesystem:** `llite`, `mdc`, `osc`, `nfs` — bytes, ops, Lustre `mds_*` / `ost_*`
- **Memory & NUMA:** `mem`, NUMA meminfo fields on `mem`, `numa`, `vm`
- **Power:** `intel_rapl`, `amd64_rapl`, `cpu_counter_metrics` (`DCGM_CPU_POWER_*`), GPU power fields
- **Process footprint:** `proc` — `VmRSS`, `VmHWM`, …

---

## Universal vs explicit code references

Every event name below is stored in **`host_data.event`** when the monitor emits it (subject to site `exclude_types` / hardware maps). All such rows flow through the **universal pipeline** in the table above through the ORM model.

The **Additional references** subsection per variable lists repository files that contain a **string literal** with that event name (metrics, plots, tests, metadata). It excludes the generated `variableMetadataMonitorEvents.js` blob and the generator script’s description table, so you see *behavioral* references only. Files named `test_*.py` under `analysis/plot/` are unit tests for plotting even when the path does not contain a `tests/` directory; treat them like other test modules when tracing usage.

**PMC note:** `CTL*` / `CTR*` (and some `V*_CTL*` / `V*_CTR*`) names appear in **`!` schema lines** in raw archives; dbload maps them to logical events (for example `INST_RETIRED`) before insert. Those logical names are what appear in `host_data.event` for PMC rows.

---

## Diagnostic guidance (events not wired into analysis)

Many counters are ingested and visible in type-detail / raw `host_data` views but are **not** rolled into default job summary plots or `metrics_data` aggregates. For those, this document adds **Diagnostic guidance**: practical ways operators and performance engineers can use rates (`delta`, `arc`) and cross-metrics checks to explain bottlenecks, faults, or imbalance. Guidance follows common Linux / HPC / fabric practice (kernel docs, vendor counter manuals, and standard wait-state interpretation).

---

## Variable catalog (alphabetical)

"""

    parts = [header]
    for k in keys:
        app, tests = _split_refs(refs[k])
        parts.append(_section_for_key(k, desc_map[k], event_to_types, app, tests))

    DOC.write_text("".join(parts), encoding="utf-8")
    print("Wrote", DOC, "keys=", len(keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
