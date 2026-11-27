# x86 counters and roofline peaks

This page explains how the monitor reads x86 core, uncore and RAPL counters, and how `host_roofline_peak` publishes hardware peaks.
It is for monitor developers and for analysis developers who interpret those keys.

On x86 the monitor reads CPU performance counters, memory-controller uncore counters and RAPL energy with its own direct `perf_event` readers.
No PMU library is built or linked on any architecture, and the monitor never reads or writes MSRs, not even as a fallback.
`x86_core_perf` opens the core events per CPU for `host_cpu_hw` (published by `cpu_counter_metrics`), `x86_uncore_perf` reads the sysfs `uncore_imc_<N>`, `uncore_hbm_<N>` and `amd_df` PMUs, and `x86_rapl` reads the perf `power` PMU with a per-domain powercap (`energy_uj`) fallback.
The Grace readers use the same model; [the design page](design.md#scaled-counters-and-pmu-sharing) explains scaling and sharing.

## Backend selection

Configure selects the CPU counter backend with `--with-cpu-counter-backend={auto|x86|arm}`.
`--with-cpu-counter-backend=x86` is the `auto` choice (the default) on x86 hosts; `auto` is `arm` elsewhere, and `x86` is rejected on non-x86 hosts.
The CPU vendor (Intel or AMD) is read at runtime; no compile-time vendor switch exists.
The x86 backend defines `MONITOR_CPU_BACKEND_X86` (Automake conditional `CPU_BACKEND_X86`).
Every type begins in plain registry order, `host_cpu_hw` included, because each direct perf reader opens its own fds (`test_stats_runtime_begin_order`).
The capability slug carries `x86perf` for the x86 backend, for example `x86_64-ver3.1-hw-ib-nvgpu-intelgpu-lustre-beegfs-x86perf-sent1-slowtier1`.

## Read model

- One fd per event, no groups, opened at startup or SIGHUP only and read each tick with `time_enabled` / `time_running` scaling.
- Fail soft: a missing PMU or a failed open leaves `host_cpu_hw` and the uncore and RAPL types enabled.
  Each key without a counter emits its value sentinel (`NOT_PRESENT`, `PERMISSION_DENIED`, `PMU_BUSY`, `READ_FAILED`, `NOT_SCHEDULED`, `COUNTER_RESET`), and one journal line names the event.
- Shared counter keys are `W=48` (lifetime modulo 2^48); `fp_ops_retired`, `amd_fp_dp_ops` and the `pmu_*` times are `W=64`.

## Core events

`x86_core_perf.c` opens one fd per event per CPU on the sysfs `cpu` PMU and takes `instructions`, `cpu-cycles` and `ref-cycles` from sysfs and every other event from a per-class raw row verified on that class.

| Class                               | Keys filled                       |
| ----------------------------------- | --------------------------------- |
| Intel SKX, CLX, ICX, SPR, EMR, GNR  | Intel core set (below)            |
| AMD Milan, Genoa (Zen3, Zen4)       | AMD core set (below)              |
| AMD Turin (Zen5)                    | AMD core set plus `amd_fp_dp_ops` |
| Other x86 (SRF, Rome, client parts) | Generic events only               |

The Intel core set is:

- `instr_retired_any` and `instr_retired` (instructions).
- `cycles_unhalted_core` and `aperf` (cycles); `cycles_unhalted_ref` and `mperf` (ref-cycles).
- `l1d_replacement`.
- `mem_load_uops_retired_{l1,l2,llc}_hit` (`0xd1/0x01,0x02,0x04`).
- The eight `fp_arith_inst_retired_*` widths.

The AMD core set is `retired_instructions`, `retired_branch_instr`, `retired_misp_branch_instr`, `ls_dispatch` and `fp_ops_retired` (total FLOPs), plus `instr_retired` and `aperf` from the generic events.
On Turin `amd_fp_dp_ops` counts packed and scalar double-precision FLOPs, so single precision is total minus double.

On other x86 classes only the generic events report (Intel: the instructions, cycles and ref-cycles key pairs; AMD: `instr_retired` and `aperf`), one journal line says so, every other key is `NOT_PRESENT`, and `pmu_solo_running_time` is not published.

On AMD the Intel fixed-counter names (`instr_retired_any`, `cycles_unhalted_core`, `cycles_unhalted_ref`) and `mperf` emit `NOT_PRESENT`; Genoa has no `ref-cycles`.
Util, clock, `arm_*` and `dram_chan*` keys emit `NOT_PRESENT` on x86 rows: Grace fills the util, clock and `arm_*` keys, and AMD DRAM traffic is on the `amd_x86_uncore_df_*` `chan<N>` rows.

Idle CPUs may show flat rows, so judge counter health under load, for example `taskset -c <CPU> bash -c 'while true; do :; done'`.
With `@full` tier tokens a census awk reads the CPU as `$2` and the Intel fixed counters as `$10 $11 $12`.

### PMU sharing on x86

`host_cpu_hw` rows carry `pmu_enabled_time`, `pmu_running_time` and, on a class with an event row, `pmu_solo_running_time`.
Intel fixed-counter events count as always running.
General events share 8 counters per CPU on Intel, which assumes Hyper-Threading is off (true on every measured node; with HT on only 4 are free and the solo share is overstated), and 6 on AMD, where event `0x03` FLOPs use a merged pair (two slots).
The kernel NMI watchdog holds Intel's fixed cycles counter, so on nodes with `nmi_watchdog=1` the cycles event multiplexes and shows up as outside sharing; that is expected.

## Uncore DRAM counters

`x86_uncore_perf.c` reads per-generation profiles, sysfs event names first, else verified raw terms placed through the PMU `format/` files; it assumes no box count and opens one fd per (event, cpumask CPU).

- `intel_x86_uncore_imc_{skx,icx,spr,emr,gnr,srf}` read CAS counts on every `uncore_imc_<N>` PMU (`mbox<N>` rows; ICX keeps `mdev<N>`).
- SPR and EMR also read `hbm_cas_*` on every `uncore_hbm_<N>` (`hbm<N>` rows, up to 32); each HBM CAS is 32 bytes.
- GNR and SRF read scheduler 0 (`CAS_COUNT_SCH0`).
- `amd_x86_uncore_df_{rome,milan,genoa,turin}` read 64-byte DRAM data beats on `amd_df`, one `chan<N>` row per DRAM channel: 8 on Rome and Milan, 12 on Genoa and Turin.
- Zen2 and Zen3 count read and write in one event (`dram_data_beats`); Zen4 and Zen5 split `dram_read_beats` and `dram_write_beats`.

Each row sums every CPU of its PMU's `cpumask` (one per socket or die), so a row covers all sockets, and each row carries `pmu_enabled_time` and `pmu_running_time`.
SKX `dram_act_count` and `dram_pre_count_miss` and the DF rows of Rome channels 2-7 emit `NOT_BUILT`, because no hardware-verified encoding exists.
Turin channels 0-11 are verified, and Genoa channels 0-11 are verified on c318-005 (ratio 0.982).
On el8 kernels `amd_df` counts socket 0 only whatever CPU is named (LS6 Milan); begin logs that once.

Analysis roofline and memory-bandwidth metrics probe `dram_cas_*` only, so HBM-primary SPR nodes also need the `hbm_cas_*` pairs on the analysis side.

## RAPL energy

`intel_x86_rapl` and `amd_x86_rapl` (`x86_rapl.c`) publish one row per socket; [source modules](../reference/source-modules.md) lists their keys and units.

## CPU classes

Intel types target SKX and later server parts:

| Class           | CPUID family_model     |
| --------------- | ---------------------- |
| Skylake-X       | `06_55`, stepping < 5  |
| Cascade Lake    | `06_55`, stepping >= 5 |
| Ice Lake server | `06_6a`, `06_6c`       |
| Sapphire Rapids | `06_8f`                |
| Emerald Rapids  | `06_cf`                |
| Granite Rapids  | `06_ad`                |
| Sierra Forest   | `06_af`                |

Intel CHA and CBOX collectors are not shipped, and Sandybridge through Broadwell are not classified.
EPYC types use server model ranges only (`amd_cpuid_match.c`).

## Roofline peaks

`host_roofline_peak` publishes per-host peak FLOP rates and memory and I/O bandwidths, including `cpu_peak_hbm_bw_bytes_per_s` from the EDAC HBM and DDR split, with `peak_calc_version` 2.
Each peak names its source.

| `gpu_peak_source` | Meaning                                           |
| ----------------- | ------------------------------------------------- |
| 0                 | Fail-open (unknown or zero)                       |
| 1                 | Probed (DRM sysfs)                                |
| 2                 | Vendor NVML (GPU FLOPS)                           |
| 3                 | Vendor `nvidia-smi` (FLOPS, PCIe IO)              |
| 4                 | GPU identity table (allowlisted HBM peak by name) |

| `cpu_peak_source` | Meaning                                                                             |
| ----------------- | ----------------------------------------------------------------------------------- |
| 0                 | Fail-open                                                                           |
| 1                 | Probed (EDAC, procfs)                                                               |
| 2                 | Identity table (Grace CPU part `0xd4f`, or x86 and AMD CPUID DRAM or HBM allowlist) |

### How peaks are derived

- GPU FLOPS are NVML-first (SM count and SM clock).
  The smi fallback uses the confirmed fields `name,clocks.max.sm,compute_cap` plus the SM identity table, never `cuda.cores` or `multiprocessor.count`, which are invalid on this driver stack.
- The FP64:FP32 ratio comes from compute capability (`roofline_nvidia_fp64_ratio_from_cc`): Kepler CC 3.5 and 3.7 non-GeForce parts and GeForce TITAN use 1/3, other Kepler 1/24.
- When DRM memory attributes are empty, memory bandwidth uses the allowlisted HBM and GDDR tables.
- On GH200, DRM may expose a misleading PCIe x1 link while NVLink-C2C is the real CPU-GPU link, so the published C2C value replaces the weaker PCIe-derived I/O peak.
- HBM3e is never inferred from marketing substrings such as `120GB` in the product name; smi `memory.total` decides.
- When EDAC speeds are missing, Grace DRAM uses the CPU-part table, then the x86 and AMD CPUID DRAM or HBM identity.
  Probed Stampede3 and LS6 classes often have EDAC DIMMs without `dimm_mem_speed`, so CPU identity is the primary DRAM path there.
- CPU FLOPS come from procfs and cpufreq.

GPU names match in this order: `GH200`, then `GB200` / `B200`, then `RTX PRO 6000` / `GB202`, then `H100`, then `A100`.
Inventory fixtures live under `tests/fixtures/{stampede3,ls6,vista,horizon}_lspci_profiles/`.

### Peak constants

The identity tables hold only allowlisted datasheet or TACC values for Horizon, Vista, Stampede3 and Lonestar6; nothing is invented for other parts.

| Identity                                  | Peak key                                  | Value                                  |
| ----------------------------------------- | ----------------------------------------- | -------------------------------------- |
| GPU name contains `GB200` / `B200`        | `gpu_peak_mem_bw_bytes_per_s` per GPU     | `8e12` B/s (HBM3e)                     |
| GPU name `GB200` / `B200`                 | SM count for smi FLOPS fallback           | `148`                                  |
| GPU name contains `GH200`                 | SM count for smi FLOPS fallback           | `132`                                  |
| `GH200`, `memory.total` < 140000 MiB      | `gpu_peak_mem_bw_bytes_per_s` per GPU     | `4e12` B/s (HBM3; Vista `97871`)       |
| `GH200`, `memory.total` >= 140000 MiB     | `gpu_peak_mem_bw_bytes_per_s` per GPU     | `4.9e12` B/s (HBM3e)                   |
| GPU name contains `GH200`                 | `gpu_peak_io_link_bw_bytes_per_s` per GPU | `900e9` B/s (NVLink-C2C)               |
| GPU name contains `H100`                  | SM / HBM per GPU                          | `132` / `3.35e12` B/s                  |
| GPU name contains `A100`                  | SM / HBM per GPU                          | `108` / `1.555e12` B/s (A100-40)       |
| GPU name `RTX PRO 6000` / `GB202`         | SM / GDDR per GPU                         | `188` / `1.597e12` B/s                 |
| CPU part `0xd4f` (Grace)                  | `cpu_peak_dram_bw_bytes_per_s`            | `512e9` B/s (LPDDR5X)                  |
| `SKYLAKE_X` (EDAC speed miss)             | DRAM                                      | `256e9` B/s (Stampede3 SKX)            |
| `CASCADE_LAKE`                            | DRAM                                      | `282e9` B/s (DDR4-2933 6ch x 2)        |
| `ICELAKE_SERVER`                          | DRAM                                      | `409.6e9` B/s (Stampede3 ICX)          |
| `SAPPHIRE_RAPIDS` + model `Max`           | `cpu_peak_hbm_bw_bytes_per_s`             | `3.2768e12` B/s (2 x 1638.4 GB/s)      |
| `SAPPHIRE_RAPIDS` (non-Max, such as 8468) | DRAM                                      | `614.4e9` B/s (DDR5-4800 8ch x 2)      |
| `AMD_MILAN`                               | DRAM                                      | `409.6e9` B/s (LS6 DDR4-3200)          |
| `AMD_GENOA`                               | DRAM                                      | `921.6e9` B/s (2 x 12ch DDR5-4800)     |
| `AMD_TURIN`                               | DRAM                                      | `1.228e12` B/s (2 x 614 GB/s AMD 9555) |

### Verify peaks for a job

Select `host_data` by host and time window, never by `jid` alone (host rows often carry no accounting jid):

```sql
WITH j AS (
  SELECT host_list[1] AS host0,
         COALESCE(telemetry_first_time, start_time) AS t0,
         COALESCE(telemetry_last_time, end_time) AS t1
  FROM job_data WHERE jid = '<JID>'
)
SELECT hd.event, COUNT(*) AS n, MIN(hd.value) AS vmin, MAX(hd.value) AS vmax
FROM host_data hd CROSS JOIN j
WHERE hd.host = j.host0
  AND hd.time >= j.t0 AND hd.time <= j.t1
  AND hd.type IN ('host_roofline_peak', 'roofline_hw_peak')
  AND hd.event LIKE '%peak%'
GROUP BY 1 ORDER BY 1;
```

`<JID>` is the Slurm job id.

## Alternatives and trade-offs

- A PMU library would give one event namespace across vendors, but it adds a link dependency and programs counters behind the monitor's back; direct perf keeps every event row verified per class.
- MSR access would reach counters perf does not expose, but it needs raw device access and bypasses kernel multiplexing.
- Allowlisted peak constants are exact for the listed parts and absent elsewhere; probing or guessing other parts would publish wrong ceilings.

## Related

- [Monitor daemon design](design.md)
- [Source modules](../reference/source-modules.md)
- [Metrics reference](../METRICS_REFERENCE.md)
- [Legacy formats](../reference/legacy-formats.md)
- [Run a correctness campaign](../how-to/run-correctness-campaign.md)
  </content>
  </invoke>
