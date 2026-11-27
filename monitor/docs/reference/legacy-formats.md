# Monitor legacy formats

This page lists how archives written by older monitor builds differ from what `hpcperfstatsd` emits today, for anyone who decodes or compares archived stats.
Consumers still read these older shapes, so each section names the current key, the legacy one, and what changes in the values.

Rename-map entries for every section live in `HPCPerfStats/hpcperfstats/dbload/lib/monitor_naming/monitor_variable_rename_map.yaml` (`types:`, `events:`, `removed_legacy:`).
Key meanings are in the [metrics reference](../METRICS_REFERENCE.md); consumer code is listed in the [monitor variables consumer map](../../../docs/reference/monitor-variables-consumer-map.md).
None of these value steps is backfilled: archived jobs keep the values their monitor build wrote.

## InfiniBand (`host_ib`)

### Merged typenames and device ids

Older archives carry the IB counters under two typenames and a slash device id:

| Legacy                                  | Current                              |
| --------------------------------------- | ------------------------------------ |
| `host_ib_ext` / `host_ib_sw` typename   | `host_ib` (one sysfs walk per cycle) |
| Device id `mlx5_0/1`                    | `mlx5_0.1`                           |
| Switch keys `rx_bytes`, `tx_bytes`, ... | `sw_rx_bytes`, `sw_tx_bytes`, ...    |

### Host-port MAD keys

Older builds queried the host port's PortCountersExtended over MAD (the 2016 `ib_ext` collector).
On Horizon GB200 (c122-018) that query timed out against the port's own LID (`BACKEND_ERROR`), and where it answered it read about 4x below the kernel counters, because fabric management clears the PortCounters behind it.
Current builds take every host counter from sysfs, and the keys below exist only in older archives:

| Legacy key                                            | Current key                             | Notes                        |
| ----------------------------------------------------- | --------------------------------------- | ---------------------------- |
| `port_xmit_pkts`, `port_rcv_pkts`                     | `port_xmit_packets`, `port_rcv_packets` | sysfs `counters/`, `E,W=64`  |
| `port_unicast_xmit_pkts`, `port_unicast_rcv_pkts`     | None                                    | nothing consumed them        |
| `port_multicast_xmit_pkts`, `port_multicast_rcv_pkts` | None                                    | nothing consumed them        |
| `port_select`, `counter_select`                       | None                                    | query metadata, not counters |

- The consumer reads `port_*_packets` first and falls back to `port_xmit_pkts` / `port_rcv_pkts` for older archives; deploy that consumer before a monitor without the MAD keys.
- In `hpcperfstats/`, only the `avg_packetsize` and `max_packetrate` job metrics (`analysis/metrics/lib/metrics.py`) read the IB packet counters, with that fallback; `avg_packetsize` scales `port_*_data` from 4-byte words to bytes (x4).
- `port_xmit_data` / `port_rcv_data` keep their names; on ports where the old MAD query answered, it overwrote them, so those ports show a one-time step where the archive switches builds.
- The switch-side `sw_*` keys stay on MAD and keep their meaning.

## Grace and aarch64 `host_cpu_hw`

### Util accumulators

`cpu_util_{total,user,sys,irq,nice}_accum_us` are CPU microseconds (util% / 100 x wall microseconds).
Older aarch64 builds (backend name `dcgm`) published util percent x microseconds, 100x the real value (Horizon c122-018: DCGM user time was 100.04x `/proc/stat` user time on loaded CPUs).
Key names and the `E` schema are the same, so archives show a 100x step at the upgrade.
No `hpcperfstats/` code reads these keys; a reader that derives seconds divides by 1e6 and, for current builds, not also by 100.

### Counter keys from the 3.0 LIKWID overlay

Monitor 3.0 read Grace counters through a LIKWID overlay without enabled / running scaling.
On GB200 and GH200, `nvidia-powerd` holds most Grace core PMCs, so those reads were low by the time-sharing factor: about 3.3x (3.2-3.5x) on a GB200 node with `nvidia-powerd` (c122-018, same DGEMM, normalised by GFLOP/s).
Current scaled counts are accurate to about 1-2% even when `nvidia-powerd` leaves the monitor 17% of the counter time (`scripts/canary_counter_check.sh` against `perf stat` and the GEMM's own FLOP rate).
GH200 and Grace-Grace steps are not measured; the few GH200 3.0 samples without `nvidia-powerd` also rose (roughly 1.4-1.8x), so do not assume nodes without `nvidia-powerd` are unchanged.

| 3.0 archives                                                                          | Current                                                         | Notes                                      |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------ |
| `instr_retired`, `aperf` / `mperf` / `cpu_clock_est_cycles` (measured part), unscaled | same keys, scaled                                               | rate step of about 3.3x on GB200           |
| None                                                                                  | `arm_fp_dp_spec`, `arm_fp_sp_spec`, `arm_fp_hp_spec` (`E,W=64`) | FP instructions by precision (`FP_*_SPEC`) |
| `arm_est_flops` = scalar SP + DP, always 0                                            | `arm_est_flops` = `FP_FIXED_OPS_SPEC` + `FP_SCALE_OPS_SPEC`     | measured FLOPs                             |
| `arm_int8_ops`, `arm_int16_ops`, always 0                                             | `ASE_SVE_INT8_SPEC`, `ASE_SVE_INT16_SPEC`                       | SVE integer instructions, W=48             |
| `arm_aarch64_imc` from the overlay group (`PENDING` until it was up)                  | own perf fds per `nvidia_scf_pmu_<N>`                           | same keys and units (64-byte CAS)          |

- Widths are the same in both: W=48 keys carry the lifetime modulo 2^48, `cpu_clock_est_cycles` is W=64.
- `fp_arith_inst_retired_scalar_{single,double}` are Intel event names and stay 0 on Grace in both.
- GH200 nodes report one SCF socket.
- The analysis side reads the FP keys as `avg_fp{64,32,16}_instr`.

### PMU sharing keys

`pmu_enabled_time`, `pmu_running_time` and `pmu_solo_running_time` (`E,W=64,U=ns`) on `host_cpu_hw`, and the first two on `arm_aarch64_imc`, are absent from archives older than the builds that publish them.
Analysis reports them as `avg_pmu_running_pct` and `avg_pmu_outside_pct` from `METRICS_CONTRACT_VERSION` 9; deploy that analysis before an RPM that carries the keys.
The keys and their arithmetic are explained in [design](../explanation/design.md).

## x86 `host_cpu_hw`, uncore and RAPL (3.0 LIKWID archives)

Monitor 3.0 read x86 counters through LIKWID; current builds use the direct perf readers in [x86 counters and roofline](../explanation/x86-counters-and-roofline.md).
Type, device and key names are the same (new keys are additive), but the values below differ.
Deploy the analysis release with `METRICS_CONTRACT_VERSION` 12 together with the first direct-perf RPM; that contract also halves SPR Max HBM bandwidth, because `hbm_cas_*` are 32-byte CAS.

### Core counters

| Key or event                                                      | 3.0 archives                                   | Current                                                                      |
| ----------------------------------------------------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------- |
| `mem_load_uops_retired_l1_hit` on ICX / SPR / EMR / GNR           | all loads (`MEM_INST_RETIRED.ALL_LOADS` alias) | L1 hits (`0xd1/0x01`), as on SKX / CLX                                       |
| `mem_load_uops_retired_l2_hit`, `_llc_hit` on ICX and later       | `NOT_BUILT`                                    | L2 / L3 hits (`0xd1/0x02`, `0x04`)                                           |
| Intel `fp_arith_inst_retired_*`                                   | `NOT_BUILT` (zeros before sentinels)           | real counts (`0xc7`, umasks `0x01`-`0x80`)                                   |
| AMD `instr_retired`, `aperf`                                      | `NOT_BUILT`                                    | generic instructions / cycles                                                |
| `fp_ops_retired` (`E,W=64,U=FLOP`), Milan / Genoa / Turin         | absent                                         | total retired SSE/AVX FLOPs                                                  |
| `amd_fp_dp_ops` (`E,W=64,U=FLOP`), Turin                          | absent                                         | DP FLOPs; single = `fp_ops_retired` - `amd_fp_dp_ops` (bf16 lands in single) |
| `pmu_enabled_time` / `pmu_running_time` / `pmu_solo_running_time` | absent                                         | sharing keys, same arithmetic as Grace                                       |

- Do not compare `mem_load_uops_retired_l1_hit` across the upgrade: current values are lower (hits only).
- On AMD, `instr_retired_any`, `cycles_unhalted_core`, `cycles_unhalted_ref` and `mperf` are `NOT_PRESENT` in both.
- 3.0 core counters were unscaled and re-armed every tick, so another PMU holder could zero or steal them and values read low under contention; current values are scaled estimates, never re-armed, and no fleet-wide factor is measured.

### Uncore DRAM counters

| Rows                                        | 3.0 archives                                                      | Current                                            |
| ------------------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------- |
| Intel IMC / HBM (`dram_cas_*`, `hbm_cas_*`) | LIKWID uncore group, about one socket's traffic on 2-socket nodes | each row sums its PMU `cpumask` CPUs (all sockets) |
| Zen4 / Zen5 `dram_chanN_bytes`              | read beats                                                        | read + write beats                                 |
| AMD DF                                      | one `df` row, `dram_chan0_bytes` .. `dram_chan3_bytes`            | one `chan<N>` row per DRAM channel                 |

- Intel IMC / HBM values step up about 2x on 2-socket nodes; current builds read every `uncore_imc_<N>` PMU (no MBOX0-11 limit) and up to 32 `hbm<N>` rows on SPR / EMR.
- Zen2 / Zen3 use one combined event and show no read + write step.
- The 3.0 DF keys held 64-byte beats despite their names and covered channels 0-3 only; current `chan<N>` rows carry `dram_data_beats` (Zen2 / Zen3, read + write) or `dram_read_beats` / `dram_write_beats` (Zen4 / Zen5).
- Milan channels 4-7 were never read, so 3.0 DF bandwidth read half (c317-016: daemon 0.506 of the load's bytes; all 8 channels 0.997).
- Turin and Genoa rows 4-11 carry beats in current builds, so DF bandwidth steps up about 3x (c573-002 Turin: all 12 channels 0.986 of the read load's bytes, channels 0-3 alone 4/12; c318-005 Genoa: all 12 channels 0.982, channels 0-3 alone 0.328).
- Consumers sum the beat keys over every `chan<N>` row (x 64 B) and dual-read the 3.0 keys; deploy that consumer with or before the monitor.
- IMC / HBM / DF rows add `pmu_enabled_time` / `pmu_running_time` (`E,W=64,U=ns`), with no solo key.
- `amd_df` on el8 (LS6) reads socket 0 only in both and logs that at begin.

### RAPL energy

`intel_x86_rapl` / `amd_x86_rapl` keys keep `E,W=32,U=mJ`.
3.0 archives hold each read's energy window; current builds publish a running total in mJ since begin, modulo 2^32, so delta / delta-t is mW and delta / (1000 delta-t) is watts.

### Typenames only in older archives

Older archives can carry `st_name`s that no current build emits: `intel_x86_pmc_gpr4` / `gpr8` and `amd_x86_pmc` (MSR core counters), bare `amd_x86_uncore_df` (native-MSR DF), and `intel_x86_pcu` plus the pre-SKX Intel uncore types.
Current core and uncore data for those CPUs is in `host_cpu_hw` and the family-named `intel_x86_uncore_imc_*` / `amd_x86_uncore_df_*` types.

### Build markers

- 3.0 capability slugs carry the token `likwid` and the flags `likwid_overlay` / `papi_hybrid`; current x86 builds carry `x86perf` with `configure.cpu_backend: "x86"` (for example `x86_64-ver3.1-hw-ib-nvgpu-intelgpu-lustre-beegfs-x86perf-sent1-slowtier1`).
- Consumers treat x86 `host_cpu_hw` chains as bridgeable only with `x86perf`.
- In the overhead harness, build-info `cpu_backend` `likwid` (3.0 x86) resolves like `x86` and `dcgm` (older aarch64) like `arm` (`measurement/lib/driver_map.py`).
- The 3.0 LIKWID collectors themselves are recorded in [LIKWID migration](../../../docs/history/likwid-migration.md).

## GPU FLOP and peak keys

- `gpu_flops` / `gpu_flops_rate` on RTX PRO 6000 (CC 12.0) and GB200 (CC 10.0) step up for tensor-heavy jobs from the 3.1 build that carries measured HMMA / DMMA peaks (2026-11-23); for example RTX PRO 6000 about 4.8e11 -> 3.9e14 FLOP/s per GPU under a 94% HMMA load, GB200 about 1.5e15 FLOP/s under a 95% HMMA load and 3.8e13 under a 95% DMMA load, where those pipes added nothing in older archives.
- On Kepler hosts (K20 / K40 / K80), `gpu_peak_fp64_flops_per_s` is about 10.7x higher than in archives from monitors that applied a 1/32 FP64 ratio to every part.

## Power telemetry

Older archives carry per-CPU and per-GPU power keys that current builds publish per socket on `host_power_socket`:

| Legacy key                                                                       | Current key                          | Notes                                                                                                     |
| -------------------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `host_cpu_hw` `dcgm_cpu_power_util_w` (DCGM 1130, same on every CPU of a socket) | `host_power_socket` `cpu_power_w`    | Horizon GB200 archives show 0 (DCGM reads no Grace power); the hwmon meter reads about 42-46 W per socket |
| `host_cpu_hw` `dcgm_cpu_power_limit_w` (DCGM 1131)                               | None                                 | always 0 on Grace; no `power1_cap` exists                                                                 |
| `nvidia_gpu` `module_power_usage` (DCGM 1133, per GPU)                           | `host_power_socket` `module_power_w` | GH200 only (about 130 W); 0 on GB200 and Grace/Grace                                                      |
| `nvidia_gpu` `sysio_power_usage` (DCGM 1132, per GPU)                            | `host_power_socket` `sysio_power_w`  | SoC rail per socket (about 0.6 W GH200, 6 W GB200)                                                        |
| None                                                                             | `host_power_socket` `socket_power_w` | whole socket: GB200 88-91 W, Grace/Grace 67-76 W                                                          |

- For jobs with only the legacy keys, the analysis node power estimate keeps the legacy formula.
- With the current keys it is the sum of `module_power_w` when that is above 0 (GH200), else the sum of `socket_power_w` plus GPU power (GB200; `socket_power_w` alone on GPU-less Grace/Grace and Vera), which adds the SoC and memory share of the socket.
- How consumers read the current per-socket rows is in [source modules](source-modules.md).
