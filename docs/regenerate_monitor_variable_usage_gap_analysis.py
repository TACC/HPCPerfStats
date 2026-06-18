#!/usr/bin/env python3
"""Regenerate artifacts/monitor-variable-usage-gap-analysis.md from monitor KEYS + usage scan.

Run from HPCPerfStats/:
  .venv/bin/python docs/regenerate_monitor_variable_usage_gap_analysis.py
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MONITOR_SRC = REPO / "monitor" / "src"
OUT = REPO / "artifacts" / "monitor-variable-usage-gap-analysis.md"

MARKERS = (
    "#define KEYS",
    "#define ARM_IMC_KEYS",
    "#define CPU_COUNTER_METRICS_KEYS",
    "#define EVENT_KEYS",
    "#define BYTE_KEYS",
    "#define XPRT_KEYS",
    "#define KNL_KEYS",
    "#define HT_KEYS",
    "#define DF_KEYS",
    "#define CTR_KEYS",
)

SKIP_NAMES = frozenset({"uint32_t", "uint64_t", "k", "t", "m", "f", "n"})

USAGE_ROOTS = [
    REPO / "hpcperfstats",
    REPO / "tests",
]

SKIP_USAGE_FILES = frozenset(
    {
        "hpcperfstats/site/frontend/src/utils/variableMetadataMonitorEvents.js",
        "hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py",
    }
)

USEFULNESS: dict[str, tuple[str, str]] = {
    "CTL": ("control register selector for configured hardware counter", "Low / compute"),
    "CTR": ("raw hardware counter value", "Medium / compute"),
    "0x": ("raw uncore event-select register value", "Medium / memory"),
}


def extract_x_keys(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        s = line.strip()
        if s.startswith("#") or not s.startswith("X("):
            continue
        m = re.match(r"X\(\s*([A-Za-z0-9_]+)\s*,", s)
        if not m:
            continue
        k = m.group(1)
        if k not in SKIP_NAMES and len(k) >= 2:
            out.add(k)
    return out


def macro_block(lines: list[str], start: int) -> list[str]:
    block: list[str] = []
    for j in range(start, min(start + 220, len(lines))):
        block.append(lines[j])
        if j > start and lines[j].strip().startswith("#endif"):
            break
        if j > start and lines[j].strip().startswith("struct stats_type"):
            break
    return block


def _macro_define_paths(name: str, prefer: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if prefer is not None:
        candidates.append(prefer)
        stem_h = prefer.with_suffix(".h")
        if stem_h.is_file() and stem_h not in candidates:
            candidates.append(stem_h)
        try:
            text = prefer.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for inc in re.findall(r'#include\s+"([^"]+)"', text):
            inc_path = MONITOR_SRC / inc
            if inc_path.is_file() and inc_path not in candidates:
                candidates.append(inc_path)
    for path in sorted(MONITOR_SRC.glob("*.h")) + sorted(MONITOR_SRC.glob("*.c")):
        if path not in candidates:
            candidates.append(path)
    return candidates


def keys_from_macro_name(
    name: str,
    cache: dict[tuple[str, str], set[str]],
    prefer: Path | None = None,
) -> set[str]:
    cache_key = (name, str(prefer) if prefer else "")
    if cache_key in cache:
        return cache[cache_key]
    keys: set[str] = set()
    for path in _macro_define_paths(name, prefer):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if not re.match(rf"#define\s+{re.escape(name)}\b", line.strip()):
                continue
            block_keys = extract_x_keys(macro_block(lines, i))
            if block_keys:
                keys |= block_keys
            elif name == "KEYS" and line.strip().startswith("#define KEYS"):
                tail = line.strip().split("KEYS", 1)[-1].strip()
                for part in re.split(r"[,\\]", tail):
                    part = part.strip()
                    if part:
                        keys |= keys_from_macro_name(part, cache, prefer=path)
            else:
                keys |= block_keys
            cache[cache_key] = keys
            return keys
    cache[cache_key] = keys
    return keys


def keys_in_file(path: Path, macro_cache: dict[tuple[str, str], set[str]]) -> set[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    keys: set[str] = set()
    for i, line in enumerate(lines):
        st = line.strip()
        if any(st.startswith(m) for m in MARKERS):
            block_keys = extract_x_keys(macro_block(lines, i))
            if block_keys:
                keys |= block_keys
            elif st.startswith("#define KEYS"):
                tail = st.split("KEYS", 1)[-1].strip()
                for part in re.split(r"[,\\]", tail):
                    part = part.strip()
                    if part:
                        keys |= keys_from_macro_name(part, macro_cache, prefer=path)
    for line in lines:
        for m in re.finditer(r"JOIN\((\w+)\)", line):
            keys |= keys_from_macro_name(m.group(1), macro_cache, prefer=path)
    return keys


def collect_emitted_by_type(*, include_ingest_aliases: bool = True) -> dict[str, set[str]]:
    by_type: dict[str, set[str]] = defaultdict(set)
    macro_cache: dict[tuple[str, str], set[str]] = {}

    type_files: list[tuple[str, Path]] = []
    for path in sorted(MONITOR_SRC.glob("*.c")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'\.st_name\s*=\s*"([^"]+)"', text):
            type_files.append((m.group(1), path))

    for st_name, path in type_files:
        by_type[st_name] |= keys_in_file(path, macro_cache)
        stem = path.stem
        hdr = MONITOR_SRC / f"{stem}.h"
        if hdr.is_file():
            by_type[st_name] |= keys_in_file(hdr, macro_cache)

    # NFS composed op keys (lowercase after naming migration)
    for op in ("read", "write"):
        for suf in ("ops", "timeouts", "queue", "rtt"):
            by_type["host_nfs"].add(f"{op}_{suf}")

    if include_ingest_aliases:
        # Legacy ingest-decoded aliases (pre-rename downstream); omit for pure monitor inventory.
        stp = REPO / "hpcperfstats/dbload/lib/sync_timedb_parsing.py"
        if stp.is_file():
            stp_text = stp.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(
                r'^([a-z0-9_]+_eventmap)\s*=\s*\{([^}]+)\}',
                stp_text,
                re.MULTILINE | re.DOTALL,
            ):
                typ = m.group(1).removesuffix("_eventmap")
                for ev_m in re.finditer(
                    r'["\']([A-Za-z0-9_]+)(?:,W=|=)',
                    m.group(2),
                ):
                    by_type[typ].add(ev_m.group(1))
                for ev_m in re.finditer(
                    r':\s*["\']([A-Za-z0-9_]+),',
                    m.group(2),
                ):
                    by_type[typ].add(ev_m.group(1))
            if "intel_x86_pmc_gpr8" in by_type:
                by_type["intel_x86_pmc_gpr4"] |= by_type["intel_x86_pmc_gpr8"]

    # osc exists in source but is not registered in stats_registry
    if (MONITOR_SRC / "osc.c").is_file():
        text = (MONITOR_SRC / "osc.c").read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines()):
            if line.strip().startswith("#define KEYS"):
                by_type["osc"] |= extract_x_keys(macro_block(text.splitlines(), i))

    return dict(by_type)


def collect_global_used_keys() -> set[str]:
    used: set[str] = set()
    for root in USAGE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in (".py", ".js", ".jsx"):
                continue
            if "node_modules" in path.parts or "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(REPO))
            if rel in SKIP_USAGE_FILES or rel.startswith("monitor/"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r'["\']([A-Za-z0-9_][A-Za-z0-9_.]*)["\']', text):
                used.add(m.group(1))
    return used


def key_description(key: str) -> str:
    if re.fullmatch(r"CTL\d+", key):
        return USEFULNESS["CTL"][0]
    if re.fullmatch(r"CTR\d+", key):
        return USEFULNESS["CTR"][0]
    if key.startswith("0x"):
        return USEFULNESS["0x"][0]
    if key.startswith("MBW_CHANNEL_"):
        return "AMD memory bandwidth counter for one DF DRAM channel"
    if key.startswith("EVENT_DRAM_CHANNEL_"):
        return "raw AMD DF DRAM channel counter before ingest decode"
    if key.startswith("FP_ARITH") or key in ("FLOPS", "MERGE", "INST_RETIRED"):
        return "hardware performance counter for floating-point or retirement"
    if key in ("APERF", "MPERF", "FIXED_CTR0", "FIXED_CTR1", "FIXED_CTR2"):
        return "CPU fixed counter or frequency proxy"
    return "monitor-emitted telemetry field"


def usefulness(key: str) -> str:
    if re.fullmatch(r"CTL\d+", key):
        return USEFULNESS["CTL"][1]
    if re.fullmatch(r"CTR\d+", key):
        return USEFULNESS["CTR"][1]
    if key.startswith("0x"):
        return USEFULNESS["0x"][1]
    if key.startswith(("port_", "Port", "VL15")):
        return "Medium / network"
    if key.startswith("pg") or key.startswith("thp_"):
        return "Low / memory"
    if key.startswith("gpu_") or key in ("tensor_active", "mem_util", "power_usage"):
        return "Medium / GPU"
    return "Low / other"


def build_section5() -> str:
    """Curated strict wiring inventory from metrics/plot paths (manual maintenance hook)."""
    return r"""## 5) Strict per-type, per-variable used inventory

This section is a strict `host_data.type` inventory for variables actively wired into current metrics/plots/displays (not just grouped families). For each type, variables listed are the monitor keys consumed by active compute or display paths.

### `amd64_df`
- **Used variables:** `MBW_CHANNEL_0`, `MBW_CHANNEL_1`, `MBW_CHANNEL_2`, `MBW_CHANNEL_3`, `MBW_CHANNEL_4`, `MBW_CHANNEL_5`, `MBW_CHANNEL_6`, `MBW_CHANNEL_7`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_mbw`, `dram_bw_node_imbalance`)
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`, `dram_bw_node_imbalance`), Summary plot (`amd_mbw`), CPU roofline memory path

### `amd64_pmc`
- **Used variables:** `FLOPS`, `APERF`, `MPERF`, `INST_RETIRED`, `BRANCH_INST_RETIRED`, `BRANCH_INST_RETIRED_MISS`, `DISPATCH_STALL_CYCLES0`, `DISPATCH_STALL_CYCLES1`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, summary/roofline/heatmap plot modules
- **Figures/metrics/displays:** Job Detail Metrics (`avg_flops`, `avg_freq`), Summary plot (`amd_flops`, `amd_instr`, `amd_mcycles`, `amd_acycles`)

### `amd64_rapl`
- **Used variables:** `MSR_PKG_ENERGY_STAT`
- **Where used in code:** node power estimation helpers and metric paths
- **Figures/metrics/displays:** Job Detail Metrics (`max_node_power_est_w`, `avg_node_power_est_w`)

### `amd_gpu`
- **Used variables:** `gpu_util`, `tensor_active`, `fp16_active`, `fp32_active`, `fp64_active`, `gpu_mem_bw_bytes_rate`, `power_usage`, `clocks_event_reasons`, `gpu_count`, `mem_used_mb`, `mem_util`, `sm_occupancy`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, `hpcperfstats/analysis/metrics/lib/gpu_job_detail_summary.py`
- **Figures/metrics/displays:** Job Detail Metrics (GPU fallback paths), `detail_gpu_*`

### `arm_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_mbw`) and roofline helpers
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`), CPU roofline memory path

### `block`
- **Used variables:** `rd_sectors`, `wr_sectors`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_blockbw`)
- **Figures/metrics/displays:** Job Detail Metrics (`avg_blockbw`)

### `cpu`
- **Used variables:** `user`, `system`, `nice`, `idle`, `irq`, `softirq`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_cpuusage`, `node_imbalance`, `time_imbalance`), summary plot
- **Figures/metrics/displays:** Job Detail Metrics (`avg_cpuusage`, `node_imbalance`, `time_imbalance`), Summary plot (`cpu`)

### `cpu_counter_metrics`
- **Used variables:** `ARM_EST_FLOPS`, `ARM_DRAM_BW_BYTES`, `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`, `SSE_DOUBLE_SCALAR`, `SSE_DOUBLE_PACKED`, `SIMD_DOUBLE_256`, `APERF`, `MPERF`, `INST_RETIRED`, `DCGM_CPU_POWER_UTIL_W`, `DCGM_CPU_POWER_LIMIT_W`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, roofline and node power estimate paths
- **Figures/metrics/displays:** Job Detail Metrics (FLOP/vector/frequency/memory/power-derived rows), CPU roofline, Summary power/frequency/counter panels

### `host_ib`
- **Used variables:** `port_xmit_data`, `port_rcv_data`, `port_xmit_pkts`, `port_rcv_pkts`, sysfs error counters, `sw_rx_bytes`, `sw_tx_bytes`, `sw_rx_packets`, `sw_tx_packets`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_ibbw`, `avg_packetsize`, `max_fabricbw`, `max_packetrate`, `fabric_node_imbalance`), summary plot hardware error overlay
- **Figures/metrics/displays:** Job Detail Metrics (fabric averages/peaks/imbalance/ratios), Summary plot (`ibbw`, IB error rates)

### `intel_4pmc3`
- **Used variables:** `APERF`, `MPERF`, `INST_RETIRED`, `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`, `SSE_DOUBLE_SCALAR`, `SSE_DOUBLE_PACKED`, `SIMD_DOUBLE_256`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, summary/roofline/heatmap plot modules
- **Figures/metrics/displays:** Job Detail Metrics (FLOP/vector/frequency rows), Summary plot (`flops64b`, `flops32b`, `instr`, `mcycles`, `acycles`, `freq`), CPU roofline and CPU multiprecision

### `intel_8pmc3`
- **Used variables:** `APERF`, `MPERF`, `INST_RETIRED`, `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`, `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`, `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`, `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`, `SSE_DOUBLE_SCALAR`, `SSE_DOUBLE_PACKED`, `SIMD_DOUBLE_256`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, summary/roofline/heatmap plot modules
- **Figures/metrics/displays:** Job Detail Metrics (FLOP/vector/frequency rows), Summary plot (`flops64b`, `flops32b`, `instr`, `mcycles`, `acycles`, `freq`), CPU roofline and CPU multiprecision

### `intel_bdw_imc`, `intel_hsw_imc`, `intel_ivb_imc`, `intel_snb_imc`, `intel_skx_imc`
- **Used variables:** `CAS_READS`, `CAS_WRITES`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_mbw`, `dram_bw_node_imbalance`), roofline helpers
- **Figures/metrics/displays:** Job Detail Metrics (`avg_mbw`, `dram_bw_node_imbalance`), Summary (`mbw`), CPU roofline memory path

### `intel_rapl`
- **Used variables:** `MSR_PKG_ENERGY_STATUS`
- **Where used in code:** node power estimation and summary power plotting paths
- **Figures/metrics/displays:** Job Detail Metrics (`max_node_power_est_w`, `avg_node_power_est_w`), Summary (`watts`, `node_power_est_w`)

### `llite`
- **Used variables:** `read_bytes`, `write_bytes`, `open`, `close`, `mmap`, `fsync`, `setattr`, `truncate`, `flock`, `getattr`, `statfs`, `alloc_inode`, `setxattr`, `listxattr`, `removexattr`, `readdir`, `create`, `lookup`, `link`, `unlink`, `symlink`, `mkdir`, `rmdir`, `mknod`, `rename`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, `hpcperfstats/analysis/metrics/lib/job_detail_fsio.py`, summary plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_sharedfs_bw`, `avg_sharedfs_iops`, `max_mds`, FSIO `detail_fsio_llite_*`), Summary (`lustre_read_mb_s`, `lustre_write_mb_s`, `liops`)

### `lnet`
- **Used variables:** `tx_bytes`, `rx_bytes`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`max_lnetbw`, `lnet_node_imbalance`)
- **Figures/metrics/displays:** Job Detail Metrics (`max_lnetbw`, `lnet_node_imbalance`)

### `mem`
- **Used variables:** `MemUsed`, `MemTotal`, `Slab`, `FilePages`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`mem_hwm`), summary plot memory panels
- **Figures/metrics/displays:** Job Detail Metrics (`mem_hwm`), Summary (`mem`)

### `net`
- **Used variables:** `rx_bytes`, `tx_bytes`, `rx_packets`, `tx_packets`, `rx_errors`, `tx_errors`, `rx_dropped`, `tx_dropped`, `collisions`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`avg_ethbw`, network fallbacks), summary error-rate builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_ethbw`, fallback fabric packet/byte rates), Summary (`summary_hardware_error_rates`)

### `nfs`
- **Used variables:** `READ_ops`, `WRITE_ops`, `normal_read`, `normal_write`, `direct_read`, `direct_write`, `server_read`, `server_write`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, `hpcperfstats/analysis/metrics/lib/job_detail_fsio.py`, summary plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_sharedfs_bw`, `avg_sharedfs_iops`, `max_mds`, FSIO `detail_fsio_nfs_*`), Summary (`nfs_read_mb_s`, `nfs_write_mb_s`, `nfs_iops`)

### `numa`
- **Used variables:** `numa_miss`, `numa_foreign`, `other_node`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (`max_numa_remote_rate`), summary NUMA panel
- **Figures/metrics/displays:** Job Detail Metrics (`max_numa_remote_rate`), Summary (`numa_remote_refs`)

### `nvidia_gpu`
- **Used variables:** `gpu_util`, `tensor_active`, `fp16_active`, `fp32_active`, `fp64_active`, `gpu_mem_bw_bytes_rate`, `power_usage`, `sysio_power_usage`, `module_power_usage`, `clocks_event_reasons`, `gpu_io_link_total_bytes`, `mem_used_mb`, `mem_util`, `sm_occupancy`, `gpu_count`, `gpu_flops`, `gpu_mem_read_bytes`, `gpu_mem_write_bytes`, `gpu_mem_total_bytes`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py`, `hpcperfstats/analysis/metrics/lib/gpu_job_detail_summary.py`, summary and roofline plot builders
- **Figures/metrics/displays:** Job Detail Metrics (`avg_gpuutil`, precision/tensor/GPU-link/GPU-power metrics, `detail_gpu_*`, GPU imbalance metrics), Summary GPU panels (`nv_*`), GPU roofline and GPU multiprecision

### `opa`
- **Used variables:** `PortXmitData`, `PortRcvData`, `PortXmitPkts`, `PortRcvPkts`, `PortXmitWait`, `SwPortCongestion`, `PortRcvFECN`, `PortRcvBECN`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/metrics.py` (fabric metrics, congestion metrics, imbalance), summary OPA/error plots
- **Figures/metrics/displays:** Job Detail Metrics (fabric fallbacks and `max_opa_congestion_rate`), Summary (`opa_wait_cong`, `opa_ecn`, `summary_hardware_error_rates`)

### `roofline_hw_peak`
- **Used variables:** `cpu_peak_fp64_flops_per_s`, `cpu_peak_dram_bw_bytes_per_s`, `gpu_peak_fp64_flops_per_s`, `gpu_peak_mem_bw_bytes_per_s`, `gpu_peak_io_link_bw_bytes_per_s`
- **Where used in code:** `hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py`, roofline plot builders
- **Figures/metrics/displays:** CPU/GPU roofline peak reference lines
"""


def build_tail() -> str:
    return r"""## 6) Where used in codebase (canonical files)

- `hpcperfstats/analysis/metrics/lib/metrics.py`: authoritative mapping from monitor keys to persisted job-level metrics (`metrics_data`) and many Job Detail table rows.
- `hpcperfstats/analysis/metrics/lib/plot/summary_metric_descriptions.py`: canonical summary-plot metric keys and user-facing descriptions for all summary subplot columns.
- `hpcperfstats/site/frontend/src/utils/variableMetadata.js`: canonical UI tooltip mapping for monitor events, derived metrics, summary metrics, and Job Detail Bokeh plot help keys.
- `hpcperfstats/site/frontend/src/utils/jobMetricDisplayLabels.js`: short-label mapping for metrics shown in Job Detail metrics table.
- `hpcperfstats/analysis/metrics/lib/job_detail_fsio.py`: filesystem detail aggregation rows shown on Job Detail.
- `hpcperfstats/analysis/metrics/lib/gpu_job_detail_summary.py`: GPU aggregate summary rows (`detail_gpu_*`) shown on Job Detail.

## 7) Figure/metric/display crosswalk for used variables

### Persisted Job Detail Metrics table (`metrics_list`)

Used variables in section 5 directly feed these metric families:

- CPU/runtime: `avg_cpuusage`, `avg_freq`, `node_imbalance`, `time_imbalance`
- FLOPs/vectorization: `avg_flops`, `vecpercent_64b`, `vecpercent_32b`, `avg_vector_width_64b`, `avg_vector_width_32b`, `flops_node_imbalance`
- Memory/NUMA: `avg_mbw`, `mem_hwm`, `max_numa_remote_rate`, `dram_bw_node_imbalance`
- Network/fabric: `avg_ethbw`, `avg_ibbw`, `avg_packetsize`, `max_fabricbw`, `max_packetrate`, `max_opa_congestion_rate`, `fabric_node_imbalance`
- Filesystem: `avg_sharedfs_iops`, `avg_sharedfs_bw`, `max_mds`, `max_lnetbw`, `lnet_node_imbalance`, plus `detail_fsio_*`
- GPU: `avg_gpuutil`, `avg_tensor_active`, `avg_fp16_active`, `avg_fp32_active`, `avg_fp64_active`, `avg_gpu_mem_bw_gbps`, `max_gpu_power`, `max_gpu_link_gbps`, `max_gpu_clock_event_reasons`, `gpu_util_node_imbalance`, `tensor_node_imbalance`, plus `detail_gpu_*`
- Ratios/power: `avg_fabric_mb_per_gflops`, `avg_fabric_mb_per_avg_tensor`, `max_node_power_est_w`, `avg_node_power_est_w`

### Summary figure subplots (Bokeh summary grid)

Used variables in section 5 feed these summary keys:

- CPU/memory: `cpu`, `mem`, `numa_remote_refs`, `mbw`, `amd_mbw`
- CPU compute/counters/power: `amd_flops`, `flops64b`, `flops32b`, `instr`, `amd_instr`, `mcycles`, `acycles`, `amd_mcycles`, `amd_acycles`, `freq`, `watts`, `cha_counter_arc_sum`
- GPU: `nv_gpu_util`, `nv_mem_used_mb`, `nv_mem_util_pct`, `nv_tensor_active`, `nv_sm_occupancy`, `nv_fp16_active`, `nv_fp32_active`, `nv_gpu_mem_bw_gbs`, `nv_power_w`, `node_power_est_w`, `nv_gpu_link_gbs`
- Filesystem/network/errors: `lustre_read_mb_s`, `lustre_write_mb_s`, `liops`, `nfs_read_mb_s`, `nfs_write_mb_s`, `nfs_iops`, `ibbw`, `summary_hardware_error_rates`, `opa_wait_cong`, `opa_ecn`

### Job Detail advanced Bokeh panels

- CPU roofline: consumes CPU FLOP and DRAM bandwidth variables (`FLOPS` / `FP_ARITH_*` / `ARM_EST_FLOPS`, plus IMC/DF/ARM DRAM bandwidth keys).
- GPU roofline: consumes GPU FLOP and link-traffic variables (`fp16_active`/`fp32_active`/`fp64_active` families and `gpu_io_link_total_bytes` paths where present).
- Multiprecision mix (CPU/GPU): consumes precision-resolved FLOP/tensor activity variables.

## 8) Notes on “all used variables”

- “All used variables” here means all monitor event keys currently wired into metric compute paths, summary plot builders, and Job Detail displays in this repository’s active code.
- Legacy labels in UI metadata (for historical data compatibility) are intentionally excluded from this section unless they map to current monitor event keys.
- Regenerate this file with `docs/regenerate_monitor_variable_usage_gap_analysis.py` after monitor or analysis changes.
"""


def main() -> int:
    emitted_by_type = collect_emitted_by_type()
    global_used = collect_global_used_keys()

    lines: list[str] = [
        "# Monitor Variable Usage Gap Analysis",
        "",
        "Static code-derived comparison between monitor-emitted schema keys in "
        "`monitor/src` and explicit quoted-key usage in `hpcperfstats/` + `tests/` "
        "(excluding `monitor/`).",
        "",
        f"*Regenerated: {date.today().isoformat()} via "
        "`docs/regenerate_monitor_variable_usage_gap_analysis.py`.*",
        "",
        "## 1) Total emitted variables (by type)",
        "",
    ]

    total_emitted = 0
    total_used = 0
    total_unused = 0

    for typ in sorted(emitted_by_type):
        keys = emitted_by_type[typ]
        used_keys = keys & global_used
        unused_keys = keys - used_keys
        total_emitted += len(keys)
        total_used += len(used_keys)
        total_unused += len(unused_keys)
        lines.append(
            f"- `{typ}`: emitted **{len(keys)}**, used **{len(used_keys)}**, "
            f"unused **{len(unused_keys)}**"
        )

    lines.extend(
        [
            "",
            "**Totals**",
            f"- Total monitor types: **{len(emitted_by_type)}**",
            f"- Total emitted variables: **{total_emitted}**",
            f"- Total explicitly used variables (quoted literals, global): **{total_used}**",
            f"- Total unused variables: **{total_unused}**",
            "",
            "## 2) Total used variables",
            "",
            f"- Explicitly used monitor variable keys (quoted literal match in usage scope): "
            f"**{len(global_used & set().union(*emitted_by_type.values()))}**",
            "",
            "## 3) Exhaustive unused variables grouped by type",
            "",
        ]
    )

    all_emitted = set().union(*emitted_by_type.values())
    global_used_events = global_used & all_emitted

    for typ in sorted(emitted_by_type):
        unused = sorted(emitted_by_type[typ] - global_used)
        if not unused:
            continue
        lines.append(f"### `{typ}` ({len(unused)})")
        for key in unused:
            lines.append(
                f"- `{key}`: {key_description(key)}. "
                f"**Usefulness:** {usefulness(key)}"
            )
        lines.append("")

    lines.extend(
        [
            "## 4) Methodology and caveats",
            "",
            "- Emitted keys = monitor `KEYS` / `stats_set` literals per `st_name` (post naming "
            "migration; no synthetic `CTL*`/`CTR*` or hex control tokens).",
            "- Usage matching scans explicit quoted key literals in `hpcperfstats/` and "
            "`tests/`; dynamically generated keys or indirect mappings can be undercounted.",
            "- Section 5 lists legacy type/event keys still probed for historical `host_data`; "
            "retired KNL/MIC collectors are not in current monitor emission.",
            "- This is static source analysis, not runtime coverage.",
            "- Compile-time gated monitor drivers may exist in source but be disabled in a "
            "given deployment.",
            "- NFS runtime-composed keys are included: `read_ops`, `read_timeouts`, "
            "`read_queue`, `read_rtt`, `write_ops`, `write_timeouts`, `write_queue`, "
            "`write_rtt`.",
            "- `osc` is parsed from `osc.c` for completeness but is **not** registered in "
            "`stats_registry.c` (not emitted by the daemon).",
            "- See also `artifacts/monitor-emitted-variables-by-architecture.md` for a "
            "subsystem/architecture inventory.",
            "",
            build_section5(),
            "",
            build_tail(),
        ]
    )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"types={len(emitted_by_type)} emitted={total_emitted} unused={total_unused}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
