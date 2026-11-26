# hpcperfstatsd overhead profile — c551-001 (x86_64, Intel(R) Xeon(R) Platinum 8468)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 2.005 [1.971, 2.039] /s | 1.166 [1.077, 1.255] core-s/h | 2879 [2875, 2884] MHz | 0.191 [0.1672, 0.2148] cores |

> baseline_drift — governor `performance` for OFF: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 1.166 [1.077, 1.255] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 27.98 [25.84, 30.13] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.03239 [0.03154, 0.03324] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0003374 [0.0003286, 0.0003462] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1467 [0.1167, 0.1767] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 1.017 [0.967, 1.067] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 3.792e-05 [-7.999e-06, 8.383e-05] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.4 [0.3891, 0.4109] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 0.6075 [0.5539, 0.6611] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 9.536 [9.352, 9.719] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 9.949 [9.766, 10.13] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 3.653 [3.49, 3.815] MiB |
| PSS steady state (`daemon_pss_mib`) | 1.59 [1.438, 1.742] MiB |
| USS steady state (`daemon_uss_mib`) | 1.531 [1.38, 1.681] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 4.723 [3.418, 6.027] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 1486 [1486, 1486] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1476 [1476, 1476] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 2.005 [1.971, 2.039] /s |
| Minor faults (`daemon_minflt_per_s`) | 4.122 [4.092, 4.153] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 62.3 [62.26, 62.33] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 3.701e+06 [3.682e+06, 3.721e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.4 [960.1, 960.6] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 2731 [2717, 2746] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 3.084e+05 [3.068e+05, 3.101e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 30.29 [29.96, 30.62] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.02715 [0.02442, 0.02987] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 3.221 [3.172, 3.27] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 0.9957 [0.7978, 1.194] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.6103 [0.567, 0.6535] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 9.408e+04 [9.402e+04, 9.415e+04] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 2.52e+04 [2.394e+04, 2.646e+04] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 3062 [-6968, 1.309e+04] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -49.99 [-800.3, 700.3] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 3.15 [2.873, 3.427] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 55.85 [23.86, 87.84] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.4035 [0.2968, 0.5101] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.027 [0.9716, 1.082] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.022 [0.9939, 1.05] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 7.556e+04 [6.244e+04, 8.867e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.01056 [0.009808, 0.01131] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.006653 [-0.03547, 0.04877] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

Node core-s/h = daemon CPU + interrupt time + helper processes the group causes (CPU counter rotation, standalone DCGM); cite it, not daemon CPU alone.

| Driver group | Types | Node core-s/h | Daemon CPU core-s/h [95% CI] | Interrupt Δ core-s/h | Helper Δ core-s/h | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| x86_perf | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr | 2066 | 0.6127 [0.5406, 0.6849] | 2066 | 0 | 0.5254 | — | — | 1 | -92.56 | 0.01223 | 1476 | 0.01362 |  |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.629 | 0.4293 [0.3791, 0.4794] | 0.1997 | 0 | 0.3681 | — | — | 1.209 | -63.51 | 0.3658 | 0 | 0.001079 |  |
| intel_gpu | intel_gpu | 0.8278 | 0.0458 [0.03746, 0.05413] | 0.782 | 0 | 0.03922 | — | — | 0.703 | -92.55 | 0.6927 | 0 | 0.0002902 |  |
| ib_mad | host_opa | 0.03484 | 0.01653 [0.01236, 0.0207] | 0.01831 | 0 | 0.01415 | — | — | 1.936 | -92.55 | 0.0003755 | 0 | 0 |  |

Framework (NONE): 0.03235 core-s/h
Interaction residual: 0.02939 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 62

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 62 | 21.7 | 21.88 | 0.1783 |
| host_proc | 62 | 2.35 | 2.375 | 0.0247 |
| lustre_osc | 62 | 2.209 | 2.227 | 0.01846 |
| host_cpu | 62 | 2.027 | 2.047 | 0.02014 |
| lustre_llite | 62 | 1.054 | 1.852 | 0.7985 |
| host_nfs | 62 | 0.8187 | 0.8253 | 0.006637 |
| host_net | 62 | 0.7781 | 0.784 | 0.005925 |
| intel_gpu | 62 | 0.7749 | 1107 | 1106 |
| intel_x86_uncore_imc_spr | 62 | 0.4099 | 0.4117 | 0.001788 |
| host_ps | 62 | 0.1855 | 0.1871 | 0.001579 |
| host_mem | 62 | 0.185 | 0.187 | 0.002022 |
| host_opa | 62 | 0.1672 | 0.1688 | 0.001594 |
| host_numa | 62 | 0.1558 | 0.1577 | 0.001924 |
| host_vm | 62 | 0.1391 | 0.141 | 0.001941 |
| host_roofline_peak | 62 | 0.1332 | 0.2046 | 0.07139 |
| lustre_mdc | 62 | 0.1314 | 0.1327 | 0.001312 |
| beegfs_client | 62 | 0.1006 | 0.1011 | 0.0004247 |
| host_tmpfs | 62 | 0.09154 | 0.09339 | 0.001861 |
| host_block | 62 | 0.07559 | 0.07559 | 3.661e-06 |
| intel_x86_rapl | 62 | 0.05037 | 0.05227 | 0.001913 |
| host_sysv_shm | 62 | 0.02761 | 0.0276 | 7.903e-07 |
| host_vfs | 62 | 0.02411 | 0.02427 | 0.0001553 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 96 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.5738 | 93,932 | 978.5 | 6.11 |
| ALL - NONE | 6 | 0.5756 | 93,971 | 978.9 | 6.13 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 815873 | hpcperfstatsd | 1.188 | 0.1601 | 1.021 | 1.981 | 0.5464 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.7841 | 1334/2876/3105 | 1.981 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.7674 | 1336/2874/3105 | 2.019 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.1876 | 1247/3800/3800 | 0.9633 |  |
| performance-b1-OFF- | performance | ok | False | 0.1734 | 1240/3800/3800 | — |  |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.2079 | 1168/3800/3800 | 1.344 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.176 | 1236/3800/3800 | 0.9733 | host_opa |
| performance-b1-SOLO-intel_gpu | performance | ok | False | 0.2172 | 1217/3800/3800 | 1.661 | intel_gpu |
| performance-b1-SOLO-x86_perf | performance | ok | False | 0.7859 | 1336/2879/3105 | 0.9644 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
| performance-b2-ALL-first | performance | ok | False | 0.7734 | 1335/2878/3105 | 1.985 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.8396 | 1320/2881/3105 | 2.039 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.2135 | 1221/3800/3800 | 0.9633 |  |
| performance-b2-OFF- | performance | ok | False | 0.1952 | 1183/3800/3800 | — |  |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.2211 | 1198/3800/3800 | 1.347 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.2055 | 1209/3800/3800 | 0.9567 | host_opa |
| performance-b2-SOLO-intel_gpu | performance | ok | False | 0.1829 | 1195/3800/3800 | 1.66 | intel_gpu |
| performance-b2-SOLO-x86_perf | performance | ok | False | 0.7792 | 1337/2881/3105 | 1.002 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
| performance-b3-ALL-first | performance | ok | False | 0.8049 | 1260/2883/3105 | 1.971 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.8384 | 1326/2883/3105 | 2.034 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.8291 | 1338/2881/3105 | 1.512 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.2161 | 1212/3800/3800 | 0.9661 |  |
| performance-b3-OFF- | performance | ok | False | 0.1745 | 1245/3800/3800 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.2209 | 1214/3800/3800 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.801 | 1339/2880/3104 | 1.997 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, intel_gpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.2282 | 1215/3800/3800 | 1.299 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.2243 | 1203/3800/3800 | 0.9639 | host_opa |
| performance-b3-SOLO-intel_gpu | performance | ok | False | 0.2461 | 1216/3800/3800 | 1.65 | intel_gpu |
| performance-b3-SOLO-x86_perf | performance | ok | False | 0.7889 | 1432/2876/3104 | 0.9633 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
