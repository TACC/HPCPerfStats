# Analysis architectures

This page describes how `hpcperfstats/analysis` metrics and plots handle each CPU and GPU vendor, which typenames they read, and in what order.
It is for analysis developers and for anyone checking why a metric is empty on one architecture.

To add support for a new CPU, follow [Add a CPU type](../how-to/develop/add-a-cpu-type.md).
The matching rule is `hpcperfstats/cursor-rules/monitor-analysis-architecture-sync.mdc`.

## Naming

Typenames and events have a canonical name and, for older data, legacy names; analysis reads canonical first, then legacy.

| Module                                                                                                      | Holds                                                                                                                   |
| ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `hpcperfstats/dbload/lib/monitor_naming/canonical.py`                                                       | canonical typenames and events (from `monitor_variable_rename_map.yaml` in the same directory), `_PMC_FREQ_BY_TYPENAME` |
| `hpcperfstats/dbload/lib/monitor_naming/legacy.py`, `hpcperfstats/dbload/lib/sync_timedb_parsing_legacy.py` | legacy names (historical `host_data`, CTL/CTR ingest)                                                                   |
| `hpcperfstats/dbload/lib/monitor_naming/resolve.py`                                                         | analysis probes: canonical first, then legacy                                                                           |
| `hpcperfstats/analysis/metrics/lib/gen/utils.py`                                                            | imports `pmc_freq_for_typename` and the `resolve.py` priorities for the per-job `utils` object                          |

### Monitor-to-analysis contract

`host_data.type` is the contract: typenames must match the shipped monitor `st_name` values for new ingest.
Historical rows may still use legacy names, and analysis dual-reads them through `resolve.py` without a DB migration.

Two-tier sparse rows: when the monitor runs with `enable_slow_tier 1`, schema lines mark slow keys with `,R=S` and sample rows use `@fast` (fast keys only) or `@full` (all keys).
Ingest in `sync_timedb_parsing.py` strips the tier markers and zips values against the matching schema subset; legacy full-width rows (no `@` token) stay supported.
Metrics pivot missing `(time, event)` pairs with NaN, so slow-tier sparsity does not invent zero counter readings at fast sample times.

## CPU

### Probe order

- `utils.utils`: the logical `pmc` uses `pmc_typename_priority()` (AMD and Intel GPR PMC before `host_cpu_hw`); IMC uses `imc_types_probe_order()`; CHA uses `cha_typename_priority()`.
- `utils.u.imc`: the first match in `imc_types_probe_order()` present in the schemas (includes the `intel_icx_imc` / `intel_spr_imc` short forms).

### Summary plot

- Intel core metrics try `intel_x86_pmc_gpr8`, then `intel_x86_pmc_gpr4`, then `host_cpu_hw`.
- Measured memory bandwidth `mbw` walks `imc_types_probe_order()` with `dram_cas_read_write_pairs()` and, on SPR, also `hbm_cas_read_write_pairs()`, summing both when present.
  Bytes per CAS are `DDR_CAS_BYTES` and `HBM_CAS_BYTES` in `gen/imc_cas_bw.py`; an HBM2e CAS moves half a DDR CAS.
- `amd_flops` reads `fp_ops_retired` from `amd_x86_pmc` or `host_cpu_hw`.
- `flops64b` / `flops32b` also take lane-weighted lowercase Intel FP_ARITH and AMD Zen5 `amd_fp_dp_ops` (FP32 = total - FP64).
- Intel CHA (optional): when `intel_x86_uncore_cha_skx` is in the job schema, the summary grid may include combined CHA `arc` rates.
  New ingest excludes CHA typenames from `host_data`.

### RAPL

`intel_x86_rapl` / `amd_x86_rapl` package energy feeds the summary `watts` / `amd_pkg_w` series (sparse `watts` is allowed, like `amd_pkg_w`).
The energy keys are running totals in mJ (`E,W=32,U=mJ`; ingest undoes the 32-bit wrap), so watts = `arc` x `RAPL_MJ_RATE_TO_W` (`columnar/summary_series.py`).
Every monitor generation published mJ (tacc_stats MSR reads were already multiplied by the energy unit), so the same factor applies to history.

### Roofline

Inputs per vendor:

- Intel FLOPS come from `core_pmc_types_probe_order()` (historical uppercase FP_ARITH or legacy SSE proxies), else the lowercase `host_cpu_hw` FP_ARITH keys times their lanes on direct-perf rows (scalar 1, 128b 2/4, 256b 4/8, 512b 8/16 for FP64/FP32).
- Intel measured bandwidth uses DDR CAS and/or SPR HBM CAS (summed when both are present).
- AMD uses the family DF (`amd_x86_uncore_df_{rome,milan,genoa,turin}`), x 64 B per beat: `AMD_DF_BEAT_EVENTS` summed over every row, the per-channel `chan<N>` rows' `dram_data_beats` or `dram_read_beats` + `dram_write_beats`, and the single `df` row's `dram_chan*_bytes` of older monitors.
  Historical data uses `amd_x86_uncore_df`/`amd64_df` + `MBW_CHANNEL_*`; FLOPS come from `amd_x86_pmc` or `host_cpu_hw`.
- Historical Intel KNL jobs may still have legacy IMC typenames in `host_data`; `roofline_peaks.py` has no KNL-specific nominal peaks.

Nominal peaks live in `hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py` (`ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS`, `infer_cpu_roofline_peak_flops_and_bw_gbps(jt)`):

- Optional true-roof contract: `host_roofline_peak` events (`cpu_peak_fp64_flops_per_s`, `cpu_peak_dram_bw_bytes_per_s`, `cpu_peak_hbm_bw_bytes_per_s`, GPU peaks) when present.
  CPU memory roof bandwidth sums DDR and HBM peaks when HBM is positive.
- Intel: one table row per canonical IMC typename in `INTEL_IMC_STATS_TYPES` (live: `intel_x86_uncore_imc_skx` / `_icx` / `_spr`).
- AMD: family DF + `host_cpu_hw` (or historical `amd_x86_pmc` + bare/`amd64_df`) -> the `amd64_epyc_2s_default` peak row.

### ARM Grace-class

`arm_aarch64_imc` (SCF CAS) and `host_cpu_hw` (`arm_est_flops`, `arm_dram_bw_bytes`; dual-read legacy `ARM_DRAM_BW_BYTES`) feed the metrics.

- Monitor 3.1 and later measure the counters by direct perf, scaled for counter sharing.
  Instruction and cycle rates read higher than monitor 3.0: about 3.3x on GB200 with `nvidia-powerd`; GH200 and Grace-Grace are not measured, so do not assume no change.
- `arm_est_flops` is measured FLOPs (FIXED + SCALE); integer ops are never folded into it, and a measured 0 is a real 0 (idle), not missing.
- Precision split: `arm_fp_{dp,sp,hp}_spec` feed `avg_fp64_instr` / `avg_fp32_instr` / `avg_fp16_instr` (Ginstr/s, FP instructions of all widths); `arm_int8_ops` / `arm_int16_ops` feed `avg_arm_int*_ops`.
- The CPU Multiprecision Mix pie is the share of those instruction rates; `avg_flops64b`/`32b` stay readable for history but are not in the pie.

### PMU sharing metrics

The Arm CPU-counter backend publishes the PMU sharing counters `pmu_enabled_time` / `pmu_running_time` / `pmu_solo_running_time` on `host_cpu_hw`; on samples without them both metrics are empty.
They are lifetime ns summed across CPUs by ingest, so they are counters rather than a per-CPU percent; x86 direct-perf builds publish the same three keys, and the metrics apply unchanged.

- `avg_pmu_running_pct` = 100 x running / enabled.
- `avg_pmu_outside_pct` = 100 x (1 - running / solo), clamped to 0-100.
- Older monitors show no data for both.

Both are staff only (`STAFF_ONLY_JOB_METRICS` in `metrics.py`): computed and persisted for every job, but the Job Detail metrics table, Extended Search metric options and job-list metric filters and histograms hide them from non-staff, because a node service (the Horizon GB200 GPU power service) keeps them high on every job.
Measured baselines (2026-11-14, `canary_counter_check.sh --sharing-only`, no load, no perf):

| Nodes                              | Running | Outside                                                    |
| ---------------------------------- | ------- | ---------------------------------------------------------- |
| Quiet Vista nodes                  | 75.3%   | 3.2% (solo 77.8%; the residual is kernel counter rotation) |
| Horizon GB200 with `nvidia-powerd` | 20.5%   | 73.6%                                                      |

## GPU

- `avg_gpuutil` prefers `nvidia_gpu` `gpu_util`, then `utilization`, then `amd_gpu` `gpu_util`, then `intel_gpu`.
- Summary plot (NVIDIA DCGM): Tensor, SM and FP pipe activity, power, HBM bandwidth rate and link `arc` bytes; `amd_gpu` when GPUPerfAPI is active.
  The `nv_tensor_dfma_active` line is the FP64 FMA pipe (DCGM DFMA): it equals FP64 on Hopper / Blackwell and is not a tensor pipe.
- Intel PVC compute busy: `avg_compute_engine_util` is the job mean of `intel_gpu` `compute_engine_util` (XPU Manager compute engine group utilization, percent, max over tiles; `canonical.INTEL_GPU_COMPUTE_ENGINE_UTIL`).
  Intel PVC jobs that ran on GPU firmware older than PVC2_1.23423 (the XPU Manager minimum) may show incorrect values.
- GPU roofline: prefer measured `gpu_mem_bw_bytes_rate` (estimated memory bandwidth) on `nvidia_gpu`, then `amd_gpu`; else PCIe/NVLink (`gpu_io_link_total_bytes` or directional bytes), or Intel PCIe + Xe Link when FLOPS exist.
  FLOPS prefer `gpu_flops` arc, else `gpu_flops_rate` (value); titles and peak preference follow the chosen axis (Memory BW vs PCIe/NvLink).

### GPU pipe metrics and the GPU Multiprecision Mix

`avg_*_active` are job means of the per-sample pipe busy percent (`nvidia_gpu`, then `amd_gpu`); a measured 0 is a value, and a sentinel or absent key is a no-data reason.

| Pipe                           | Notes                                                                                 |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| Tensor IMMA                    | INT8/INT4                                                                             |
| Tensor HMMA                    | FP16/BF16/TF32/FP8; one counter, so tensor FLOPs are estimates                        |
| Tensor DMMA                    | FP64 tensor core; NOT_PRESENT on the DCGM path, real on Hopper / Blackwell            |
| FP16, FP32, FP64               | vector pipes                                                                          |
| Integer (`avg_integer_active`) | absent on A100, busy about half the time under FP work on Hopper, near 0 on Blackwell |

The pie shows pipe busy shares, not FLOPs (idle excluded).
IMMA, HMMA and DMMA plus Tensor (unattributed) = lumped tensor minus the splits replace the lumped `tensor_active` when a split is positive.
`avg_tensor_dfma_active` stays a metric (FP64 FMA pipe, the same as FP64 on Hopper / Blackwell) but is never a wedge.

### Node power estimate

`max_node_power_est_w`, `avg_node_power_est_w` and `job_cpu_gpu_watt_hours` are computed per (host, time):

- Grace: `host_power_socket` sum of `module_power_w` when above 0 (GH200), else the sum of `socket_power_w` over socket `dev` rows + NVIDIA `power_usage` (GB200; the socket sum alone on GPU-less Grace/Grace and Vera nodes).
- x86: Intel/AMD RAPL (`intel_x86_rapl` / `amd_x86_rapl`, `pkg_energy`) + GPU.
- Rows sum by `dev` with no value dedup, so two equal sockets count twice.
- History from monitors before power round 2 dual-reads the retired keys with the old formula: `host_cpu_hw` `dcgm_cpu_power_util_w` (sum of unique per-socket watt paints per host and time) and `nvidia_gpu` `module_power_usage`.
  Horizon GB200 CPU power reads 0 in that history, so GB200 and Grace/Grace estimates from current monitors are higher by the SoC + memory share.

Ingest: `sync_timedb_parsing` keeps `dev` for the keep-`dev` collapse set (`canonical.KEEP_DEV_COLLAPSE_TYPES`: GPU types plus `host_power_socket`; Codon mirror in `dbload/codon/feed_collapse.py`), clusters history `host_cpu_hw` DCGM power rows, and max-reduces multi-GPU module power.

## Vector and width metrics

`vecpercent_*` and `avg_vector_width_*` need per-event FP counters (Intel FP_ARITH and/or legacy SSE names) and read only the historical uppercase names.
Aggregate AMD `fp_ops_retired` does not decompose by width.

x86 direct perf:

- `avg_flops` reads `host_cpu_hw` AMD `fp_ops_retired` (Zen3 / Zen4 / Zen5), lane-weighted lowercase Intel FP_ARITH, or Grace `arm_est_flops` (a positive rate wins over another family's 0).
- The CPU pie inputs `avg_fp64_instr` / `avg_fp32_instr` take Grace FP instructions, Intel FP_ARITH instruction counts (direct-perf rows only: positive `pmu_enabled_time`), or Zen5 FLOPs by precision (`amd_fp_dp_ops`, FP32 = total - FP64, bf16 in FP32).
  Zen3 / Zen4 have no split and show no CPU pie.
- `flops_node_imbalance` / `dram_bw_node_imbalance` resolve the same family as `avg_flops` / `avg_mbw` (shared family tables; the Intel lanes' PMU gate applies per host).
