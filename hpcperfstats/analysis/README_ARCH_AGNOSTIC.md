# Architecture-agnostic analysis (metrics and plots)

This note summarizes how CPU/GPU vendors are handled in `hpcperfstats/analysis` and how to extend support.

## CPU (AMD, Intel, LIKWID)

- **`utils.utils`**: Logical `pmc` is chosen with `PMC_TYPENAME_PRIORITY` (AMD and 8/4-counter Intel before `cpu_counter_metrics`). IMC is the first entry in `INTEL_IMC_STATS_TYPES` present in the job schema. CHA uses `CHA_TYPENAME_PRIORITY`.
- **Summary plot**: Intel core metrics (`flops64b`, `flops32b`, `instr`, `mcycles`, `acycles`) try `intel_8pmc3`, then `intel_4pmc3`, then `cpu_counter_metrics`. Intel DRAM `mbw` tries every IMC type in `INTEL_IMC_STATS_TYPES` (same idea as roofline).
- **Intel CHA (optional)**: When `intel_skx_cha` or `intel_knl_cha` is in the job schema, the summary grid may include a combined `arc` rate over CHA counter events (evictions / LLC lookups / bypass-to-IMC) aggregated across all CHA boxes—useful as a coarse on-node coherence/LLC pressure signal for hybrid MPI+OpenMP. Event strings must match the post-ingest schema (see `cha_event_map` in `dbload/hardware_counter_maps/intel_process.py`).
- **Roofline**: Intel FLOPS paths use `INTEL_CORE_PMC_TYPES_ORDERED` (includes `cpu_counter_metrics`). AMD needs `amd64_pmc` FLOPS plus `amd64_df` MBW channels (family 17h/19h when the monitor exposes them).
- **Heatmap CPI**: Candidate list includes Intel PMC types, `amd64_pmc`, and `cpu_counter_metrics`.

### Monitor ↔ analysis contract (`host_data.type`)

All of the typenames above are whatever the **monitor** publishes as `host_data.type` (see `HPCPerfStats/monitor/` `stats_type.st_name` and ingest). Analysis must **not** introduce parallel names: when adding Intel IMC generations, ARM IMC, or AMD paths, align `INTEL_IMC_STATS_TYPES`, `ARM_IMC_STATS_TYPES`, roofline merge logic, and tests with the monitor’s actual schema.

### Roofline nominal peaks (`roofline_peaks.py`)

- **File**: `hpcperfstats/analysis/plot/roofline_peaks.py` defines `ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS` and `infer_cpu_roofline_peak_flops_and_bw_gbps(jt)` using **`jid_table.schema`** (keys = `host_data.type` values for the job).
- **Intel**: One table row per entry in **`INTEL_IMC_STATS_TYPES`** (same strings as the monitor). Inference picks the **first** typename in that tuple present in the schema—matching roofline’s IMC bandwidth scan order.
- **AMD**: `amd64_pmc` + `amd64_df` → default 2S EPYC-class peak row (`amd64_epyc_2s_default`); Zen generation is **not** in `host_data.type`, so optional per-generation rows are for documentation/overrides only until host metadata or config exists.
- **ARM Grace-class**: `arm_imc` in schema → Grace single-die peak row; synthetic DCGM counters remain under `cpu_counter_metrics` (see monitor `cpu_counter_metrics.c`).
- **Cursor rule**: `HPCPerfStats/hpcperfstats/cursor-rules/monitor-analysis-architecture-sync.mdc` summarizes how these pieces stay in sync when the monitor or analysis changes.

## GPU (NVIDIA, AMD)

- **`avg_gpuutil`**: Prefers `nvidia_gpu` `gpu_util`, then `utilization`, then `amd_gpu` `gpu_util`. Catalog placeholder type is `gpu` (not vendor-specific).
- **Summary plot (NVIDIA DCGM)**: Beyond util and framebuffer usage, the job summary can plot tensor/SM/FP pipe activity (`tensor_active`, `sm_occupancy`, `fp16_active`, `fp32_active`), `mem_util`, `power_usage`, estimated HBM bandwidth rate (`gpu_mem_bw_bytes_rate`), and `arc` rate from `gpu_io_link_total_bytes` (PCIe + NVLink PROF bytes). **`amd_gpu`** uses the same schema names when GPUPerfAPI is available; many fields may be zero if the backend is inactive.
- **Job metrics**: Additional catalog entries cover average tensor activity, GPU memory BW (GB/s), peak power, peak link throughput (GB/s), DCGM clock-throttle bitmask (opaque integer), GPU util / tensor **node imbalance** (snapshot columns), fabric MB/s per average tensor activity (heuristic for MPI+AI), plus CPU-side `dram_bw_node_imbalance` and `lnet_node_imbalance`. **Node power estimate** (`max_node_power_est_w`, `avg_node_power_est_w`) follows the same CPU/GPU/module merge as the summary plot’s `node_power_est_w` (Intel/AMD RAPL PKG arc→W, Grace `DCGM_CPU_POWER_UTIL_W`, `nvidia_gpu` `power_usage`, and **module-only** when `module_power_usage` is positive).
- **Ingest**: `sync_timedb_parsing.compute_deltas_and_arc` clusters `cpu_counter_metrics` `DCGM_CPU_POWER_*_W` rows before insert (repeated per-core socket readings) and takes **max** across GPU devs for `module_power_usage` / `sysio_power_usage` so superchip module power is not summed twice.
- **DevPlot**: Uses `value` (not `arc`) for type-detail when `mem`, `nvidia_gpu`, or `amd_gpu` is in the type list.

## Vector / width metrics (`vecpercent_*`, `avg_vector_width_*`)

These need **per-event** FP counters (Intel FP_ARITH and/or legacy SSE names). Aggregate AMD `FLOPS` does not decompose by vector width; see class docstrings in `metrics.py`.

## New CPU types (e.g. NVIDIA Grace / Neoverse)

When the monitor adds new `host_data.type` values:

1. Add a nominal APERF/MPERF reference frequency to `_PMC_FREQ_BY_TYPENAME` in `gen/utils.py` if the type should act as PMC.
2. Append the typename to `PMC_TYPENAME_PRIORITY` in the desired precedence order.
3. Add summary/roofline/heatmap specs or event lists if the counter semantics match an existing path.
4. If the type is an **Intel IMC** DRAM counter source, append it to **`INTEL_IMC_STATS_TYPES`** (correct probe order) and add a matching row in **`roofline_peaks.py`**.
5. If the type is **ARM IMC** (`arm_imc` pattern), update **`ARM_IMC_STATS_TYPES`** and roofline ARM paths as needed.
6. Add unit tests with mocked `get_aggregate_df` / job schemas (including `test_roofline_peaks.py` when inference or peak keys change).
