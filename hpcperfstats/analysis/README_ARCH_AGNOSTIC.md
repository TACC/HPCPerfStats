# Architecture-agnostic analysis (metrics and plots)

This note summarizes how CPU/GPU vendors are handled in `hpcperfstats/analysis` and how to extend support.

## Naming (canonical + dual-read)

- **Canonical typenames/events** live in `hpcperfstats/dbload/lib/monitor_naming/canonical.py` (from `docs/monitor_variable_rename_map.yaml`).
- **Legacy names** (historical `host_data`, CTL/CTR ingest) live in `hpcperfstats/dbload/lib/monitor_naming/legacy.py` and `hpcperfstats/dbload/lib/sync_timedb_parsing_legacy.py`.
- **Analysis probes** use `hpcperfstats/dbload/lib/monitor_naming/resolve.py` (canonical first, then legacy).
- **`analysis/metrics/lib/gen/utils.py`** re-exports canonical lists (`INTEL_IMC_STATS_TYPES`, `INTEL_CORE_PMC_TYPES_ORDERED`, etc.).

## CPU (AMD, Intel, Grace)

- **`utils.utils`**: Logical `pmc` uses `pmc_typename_priority()` (AMD and Intel GPR PMC before `host_cpu_hw`). IMC uses `imc_types_probe_order()`. CHA uses `cha_typename_priority()`.
- **Summary plot**: Intel core metrics try `intel_x86_pmc_gpr8`, then `intel_x86_pmc_gpr4`, then `host_cpu_hw`. Measured memory `mbw` walks `imc_types_probe_order()` with `dram_cas_read_write_pairs()` and, on SPR, also `hbm_cas_read_write_pairs()` (sum when both present; bytes per CAS are `DDR_CAS_BYTES` and `HBM_CAS_BYTES` in `gen/imc_cas_bw.py`; an HBM2e CAS moves half a DDR CAS). `amd_flops` reads `fp_ops_retired` from `amd_x86_pmc` or `host_cpu_hw`; `flops64b` / `flops32b` also take lane-weighted lowercase Intel FP_ARITH and AMD Zen5 `amd_fp_dp_ops` (FP32 = total - FP64).
- **Intel CHA (optional)**: When `intel_x86_uncore_cha_skx` is in the job schema, the summary grid may include combined CHA `arc` rates. New ingest excludes CHA typenames from `host_data` unless product policy changes.
- **Roofline**: Intel FLOPS from `core_pmc_types_probe_order()` (historical uppercase FP_ARITH or legacy SSE proxies), else the lowercase `host_cpu_hw` FP_ARITH keys times their lanes (scalar 1, 128b 2/4, 256b 4/8, 512b 8/16 for FP64/FP32) on direct-perf rows. Measured Intel BW uses DDR CAS and/or SPR HBM CAS (sum when both). AMD uses family DF (`amd_x86_uncore_df_{rome,milan,genoa,turin}`: `AMD_DF_BEAT_EVENTS` summed over every row, the per-channel `chan<N>` rows' `dram_data_beats` or `dram_read_beats` + `dram_write_beats` and the older single `df` row's `dram_chan*_bytes`, x 64 B per beat) or historical `amd_x86_uncore_df`/`amd64_df` + `MBW_CHANNEL_*`, with FLOPS from `amd_x86_pmc` or `host_cpu_hw`. Historical Intel KNL jobs may still have legacy IMC typenames in `host_data`, but KNL-specific nominal roofline peaks were retired from `roofline_peaks.py`.
- **`utils.u.imc`**: first match in `imc_types_probe_order()` present in schemas (includes `intel_icx_imc` / `intel_spr_imc` short forms).
- **RAPL**: `intel_x86_rapl` / `amd_x86_rapl` package energy → summary `watts` / `amd_pkg_w` (sparse `watts` allowed like `amd_pkg_w`). The energy keys are running totals in mJ (`E,W=32,U=mJ`; ingest undoes the 32-bit wrap), so watts = `arc` x `RAPL_MJ_RATE_TO_W` (`columnar/summary_series.py`); every monitor generation published mJ (tacc_stats MSR reads already multiplied by the energy unit), so the same factor applies to history.

### Monitor ↔ analysis contract (`host_data.type`)

Typenames must match the **shipped** monitor `st_name` values for new ingest. Historical rows may still use legacy names; analysis dual-reads via `resolve.py` without a DB migration.

**Two-tier sparse rows:** when the monitor runs with `enable_slow_tier 1`, schema lines mark slow keys with `,R=S` and sample rows use `@fast` (fast keys only) or `@full` (all keys). Ingest in `sync_timedb_parsing.py` strips tier markers and zips values against the matching schema subset; legacy full-width rows (no `@` token) remain supported. Metrics pivot missing `(time, event)` pairs with NaN so slow-tier sparsity does not invent zero counter readings at fast sample times.

### Roofline nominal peaks (`roofline_peaks.py`)

- **File**: `hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py` — `ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS`, `infer_cpu_roofline_peak_flops_and_bw_gbps(jt)`.
- **Optional true-roof contract**: `host_roofline_peak` events (`cpu_peak_fp64_flops_per_s`, `cpu_peak_dram_bw_bytes_per_s`, `cpu_peak_hbm_bw_bytes_per_s`, GPU peaks) when present. CPU memory roof bandwidth sums DDR and HBM peaks when HBM is positive.
- **Intel**: One table row per canonical IMC typename in `INTEL_IMC_STATS_TYPES` (live: `intel_x86_uncore_imc_skx` / `_icx` / `_spr`).
- **AMD**: family DF + `host_cpu_hw` (or historical `amd_x86_pmc` + bare/`amd64_df`) → `amd64_epyc_2s_default` peak row.
- **ARM Grace-class**: `arm_aarch64_imc` (SCF CAS) and `host_cpu_hw` (`arm_est_flops`, `arm_dram_bw_bytes`; dual-read legacy `ARM_DRAM_BW_BYTES`). From monitor 3.1 the counters are measured by direct perf (scaled for counter sharing; instr/cycle rates rise versus 3.0, measured about 3.3× on GB200 with `nvidia-powerd`; GH200 / Grace-Grace not measured, do not assume no change) and `arm_est_flops` is measured FLOPs (FIXED + SCALE). Precision split: `arm_fp_{dp,sp,hp}_spec` feed `avg_fp64_instr` / `avg_fp32_instr` / `avg_fp16_instr` (Ginstr/s, FP instructions all widths); INT ops (`arm_int8_ops` / `arm_int16_ops`) feed `avg_arm_int*_ops`. The CPU Multiprecision Mix pie is the share of those instruction rates (`avg_flops64b`/`32b` stay readable for history, not in the pie). A measured 0 is a real 0 (idle), not missing. Ints are never folded into `arm_est_flops`. From monitor 3.1 Phase 1b builds (the version stays 3.1), `host_cpu_hw` also carries the PMU sharing counters `pmu_enabled_time` / `pmu_running_time` / `pmu_solo_running_time` (lifetime ns, summed across CPUs by ingest, so they are counters rather than a per-CPU percent). `avg_pmu_running_pct` = 100 × running / enabled and `avg_pmu_outside_pct` = 100 × (1 − running / solo), clamped to 0–100; `METRICS_CONTRACT_VERSION` 9. Older monitors show no data for both. x86 direct-perf builds publish the same three keys on `host_cpu_hw`, and the metrics apply unchanged (contract 12). Both are **staff only** (`STAFF_ONLY_JOB_METRICS` in `metrics.py`, user decision 2026-11-14): computed and persisted for every job, but the Job Detail metrics table, Extended Search metric options and job-list metric filters / histograms hide them from non-staff, because a node service (Horizon GB200 GPU power service) keeps them high on every job. Measured baselines (2026-11-14, `canary_counter_check.sh --sharing-only`, no load, no perf): quiet Vista nodes 75.3% running / 3.2% outside (solo 77.8%; the residual is kernel counter rotation), Horizon GB200 with `nvidia-powerd` 20.5% running / 73.6% outside.
- **Cursor rule**: `hpcperfstats/cursor-rules/monitor-analysis-architecture-sync.mdc`.

## GPU (NVIDIA, AMD, Intel PVC)

- **`avg_gpuutil`**: Prefers `nvidia_gpu` `gpu_util`, then `utilization`, then `amd_gpu` `gpu_util`, then `intel_gpu`.
- **Summary plot (NVIDIA DCGM)**: Tensor/SM/FP pipe activity, power, HBM BW rate, link `arc` bytes; **`amd_gpu`** when GPUPerfAPI is active. The `nv_tensor_dfma_active` line is the FP64 FMA pipe (DCGM DFMA): it equals FP64 on Hopper / Blackwell and is not a tensor pipe.
- **GPU pipe metrics and the GPU Multiprecision Mix (contract 14)**: `avg_*_active` are job means of the per-sample pipe busy percent (`nvidia_gpu`, then `amd_gpu`); a measured 0 is a value, a sentinel or absent key is a no-data reason. Pipes: Tensor IMMA (INT8/INT4), Tensor HMMA (FP16/BF16/TF32/FP8; one counter, so tensor FLOPs are estimates), Tensor DMMA (FP64 tensor core; NOT_PRESENT on the DCGM path, real on Hopper / Blackwell), FP16 / FP32 / FP64 and the integer pipe (`avg_integer_active`; absent on A100, busy about half the time under FP work on Hopper, near 0 on Blackwell). The pie shows **pipe busy shares, not FLOPs** (idle excluded): IMMA / HMMA / DMMA plus Tensor (unattributed) = lumped tensor minus the splits replace lumped `tensor_active` when a split is positive. `avg_tensor_dfma_active` stays a metric (FP64 FMA pipe, same as FP64 on Hopper / Blackwell) but is never a wedge.
- **Intel PVC compute busy (contract 15)**: `avg_compute_engine_util` is the job mean of `intel_gpu` `compute_engine_util` (XPU Manager compute engine group utilization, percent, max over tiles; `canonical.INTEL_GPU_COMPUTE_ENGINE_UTIL`). It is the working PVC busy signal: on GFX firmware below PVC2_1.23423 (XPU Manager minimum) the EU metrics (`sm_active`, EU Array Active) are NOT_PRESENT, while compute engine busy reads real (99 under a stress run on PVC2_1.23263).
- **GPU roofline**: Prefer measured `gpu_mem_bw_bytes_rate` (estimated mem BW) on `nvidia_gpu` then `amd_gpu`; else PCIe/NVLink (`gpu_io_link_total_bytes` or directional bytes) or Intel PCIe+Xe Link when FLOPS exist. FLOPS prefer `gpu_flops` arc, else `gpu_flops_rate` (value). Titles and peak preference follow the chosen axis (Memory BW vs PCIe/NvLink).
- **Job metrics**: Node power estimate (`max_node_power_est_w`, `avg_node_power_est_w`, `job_cpu_gpu_watt_hours`) per (host, time): Grace `host_power_socket` Σ `module_power_w` when > 0 (GH200), else Σ `socket_power_w` over socket `dev` rows + NVIDIA `power_usage` (GB200; socket sum alone on GPU-less Grace/Grace and Vera nodes); x86 is Intel/AMD RAPL (`intel_x86_rapl` / `amd_x86_rapl`, `pkg_energy`) + GPU. Rows sum by `dev` with no value dedup, so two equal sockets count twice. History from monitors before round 2 dual-reads the retired keys with the old formula: `host_cpu_hw` `dcgm_cpu_power_util_w` (sum of **unique** per-socket watt paints per host/time) and `nvidia_gpu` `module_power_usage`. Horizon GB200 CPU power read 0 before round 2; GB200 and Grace/Grace estimates rise by the SoC + memory share.
- **Ingest**: `sync_timedb_parsing` keeps `dev` for the keep-`dev` collapse set (`canonical.KEEP_DEV_COLLAPSE_TYPES`: GPU types plus `host_power_socket`; Codon mirror in `dbload/codon/feed_collapse.py`), clusters history `host_cpu_hw` DCGM power rows, and max-reduces multi-GPU module power.

## Vector / width metrics (`vecpercent_*`, `avg_vector_width_*`)

Need per-event FP counters (Intel FP_ARITH and/or legacy SSE names). Aggregate AMD `fp_ops_retired` does not decompose by width.

**x86 direct perf (contract 12):** `avg_flops` reads `host_cpu_hw` AMD `fp_ops_retired` (Zen3 / Zen4 / Zen5), lane-weighted lowercase Intel FP_ARITH, or Grace `arm_est_flops` (a positive rate wins over another family's 0). The CPU pie inputs `avg_fp64_instr` / `avg_fp32_instr` take Grace FP instructions, Intel FP_ARITH instruction counts (direct-perf rows only: positive `pmu_enabled_time`), or Zen5 FLOPs by precision (`amd_fp_dp_ops`, FP32 = total - FP64, bf16 in FP32); Zen3 / Zen4 have no split and show no CPU pie. The vector-width metrics (`vecpercent_*`, `avg_vector_width_*`) and `flops_node_imbalance` still read only the historical uppercase / `FLOPS` names.

## New CPU types

1. Update `docs/monitor_variable_rename_map.yaml`, then `canonical.py` / `legacy.py`.
2. Add `_PMC_FREQ_BY_TYPENAME` entries in `gen/utils.py` when the type acts as PMC.
3. Extend `resolve.py` probe orders if needed.
4. Add roofline peak rows for new Intel IMC typenames.
5. Add unit tests (including `test_monitor_analysis_typename_contract.py`, `test_roofline_peaks.py`).
