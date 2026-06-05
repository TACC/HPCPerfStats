# Architecture-agnostic analysis (metrics and plots)

This note summarizes how CPU/GPU vendors are handled in `hpcperfstats/analysis` and how to extend support.

## Naming (canonical + dual-read)

- **Canonical typenames/events** live in `hpcperfstats/monitor_naming/canonical.py` (from `docs/monitor_variable_rename_map.yaml`).
- **Legacy names** (historical `host_data`, CTL/CTR ingest) live in `hpcperfstats/monitor_naming/legacy.py` and `hpcperfstats/dbload/sync_timedb_parsing_legacy.py`.
- **Analysis probes** use `hpcperfstats/monitor_naming/resolve.py` (canonical first, then legacy).
- **`analysis/gen/utils.py`** re-exports canonical lists (`INTEL_IMC_STATS_TYPES`, `INTEL_CORE_PMC_TYPES_ORDERED`, etc.).

## CPU (AMD, Intel, Grace)

- **`utils.utils`**: Logical `pmc` uses `pmc_typename_priority()` (AMD and Intel GPR PMC before `host_cpu_hw`). IMC uses `imc_types_probe_order()`. CHA uses `cha_typename_priority()`.
- **Summary plot**: Intel core metrics try `intel_x86_pmc_gpr8`, then `intel_x86_pmc_gpr4`, then `host_cpu_hw`. DRAM `mbw` walks `imc_types_probe_order()` with `dram_cas_read_write_pairs()`.
- **Intel CHA (optional)**: When `intel_x86_uncore_cha_skx` or `intel_x86_uncore_cha_knl` is in the job schema, the summary grid may include combined CHA `arc` rates.
- **Roofline**: Intel FLOPS from `core_pmc_types_probe_order()` (FP_ARITH or legacy SSE proxies). AMD needs `amd_x86_pmc` + `amd_x86_uncore_df` MBW channels when exposed.
- **Heatmap CPI**: Candidate list includes Intel PMC types, `amd_x86_pmc`, and `host_cpu_hw`.

### Monitor ↔ analysis contract (`host_data.type`)

Typenames must match the **shipped** monitor `st_name` values for new ingest. Historical rows may still use legacy names; analysis dual-reads via `resolve.py` without a DB migration.

### Roofline nominal peaks (`roofline_peaks.py`)

- **File**: `hpcperfstats/analysis/plot/roofline_peaks.py` — `ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS`, `infer_cpu_roofline_peak_flops_and_bw_gbps(jt)`.
- **Optional true-roof contract**: `host_roofline_peak` events (`cpu_peak_fp64_flops_per_s`, `cpu_peak_dram_bw_bytes_per_s`, GPU peaks) when present.
- **Intel**: One table row per canonical IMC typename in `INTEL_IMC_STATS_TYPES` (e.g. `intel_x86_uncore_imc_hsw`, `intel_x86_uncore_mc_knl`).
- **AMD**: `amd_x86_pmc` + `amd_x86_uncore_df` → `amd64_epyc_2s_default` peak row.
- **ARM Grace-class**: `arm_aarch64_imc` and/or `host_cpu_hw` synthetic counters (`arm_est_flops`, `ARM_DRAM_BW_BYTES`).
- **Cursor rule**: `hpcperfstats/cursor-rules/monitor-analysis-architecture-sync.mdc`.

## GPU (NVIDIA, AMD)

- **`avg_gpuutil`**: Prefers `nvidia_gpu` `gpu_util`, then `utilization`, then `amd_gpu` `gpu_util`.
- **Summary plot (NVIDIA DCGM)**: Tensor/SM/FP pipe activity, power, HBM BW rate, link `arc` bytes; **`amd_gpu`** when GPUPerfAPI is active.
- **Job metrics**: Node power estimate (`max_node_power_est_w`, `avg_node_power_est_w`) merges Intel/AMD RAPL (`intel_x86_rapl` / `amd_x86_rapl`, `pkg_energy`), Grace `host_cpu_hw` `dcgm_cpu_power_util_w`, and NVIDIA `power_usage` / `module_power_usage`.
- **Ingest**: `sync_timedb_parsing` clusters `host_cpu_hw` DCGM power rows and max-reduces multi-GPU module power.

## Vector / width metrics (`vecpercent_*`, `avg_vector_width_*`)

Need per-event FP counters (Intel FP_ARITH and/or legacy SSE names). Aggregate AMD `fp_ops_retired` does not decompose by width.

## New CPU types

1. Update `docs/monitor_variable_rename_map.yaml`, then `canonical.py` / `legacy.py`.
2. Add `_PMC_FREQ_BY_TYPENAME` entries in `gen/utils.py` when the type acts as PMC.
3. Extend `resolve.py` probe orders if needed.
4. Add roofline peak rows for new Intel IMC typenames.
5. Add unit tests (including `test_monitor_analysis_typename_contract.py`, `test_roofline_peaks.py`).
