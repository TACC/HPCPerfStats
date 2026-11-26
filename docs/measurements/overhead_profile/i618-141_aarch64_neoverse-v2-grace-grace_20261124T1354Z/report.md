# hpcperfstatsd overhead profile — i618-141 (aarch64, Neoverse-V2)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 6.343 [6.296, 6.39] /s | 1.622 [1.497, 1.747] core-s/h | 3411 [3411, 3411] MHz | 0.03921 [0.03474, 0.04367] cores |

> baseline_drift — governor `performance` for NONE: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 1.622 [1.497, 1.747] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 38.93 [35.93, 41.94] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.04506 [0.04375, 0.04638] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0003129 [0.0003038, 0.0003221] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1467 [0.1067, 0.1867] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 1.471 [1.441, 1.5] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.0009 [0.0008761, 0.000924] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.003519 [0.003258, 0.00378] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 1.55 [1.515, 1.586] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 12.93 [12.41, 13.46] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 13.58 [12.95, 14.2] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 6.045 [4.751, 7.338] MiB |
| PSS steady state (`daemon_pss_mib`) | 3.014 [2.797, 3.231] MiB |
| USS steady state (`daemon_uss_mib`) | 2.707 [2.691, 2.723] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 6.438 [5.125, 7.75] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 1461 [1461, 1461] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1300 [1300, 1300] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 6.343 [6.296, 6.39] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.06113 [0.04946, 0.07281] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 57.32 [57.31, 57.34] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 4.753e+06 [4.734e+06, 4.772e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 944.4 [944.1, 944.6] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 3454 [3443, 3465] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 3.961e+05 [3.945e+05, 3.977e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 0.1446 [0.118, 0.1713] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.003582 [0.0007124, 0.006452] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 4.125 [4.125, 4.125] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 1.787 [1.425, 2.148] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.01068 [0.005036, 0.01633] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 1.425e+04 [1.416e+04, 1.434e+04] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 51.5 [33.39, 69.6] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | -99.1 [-337.6, 139.4] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -347.5 [-1018, 322.7] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | NOT_MEASURED (no RAPL powercap or hwmon energy counters readable) |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 57.25 [18.3, 96.21] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | -0.1173 [-0.2571, 0.02249] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 0.9988 [0.9972, 1] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1 [0.9998, 1] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 1.51e+04 [9199, 2.101e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.00817 [0.00674, 0.0096] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | -0.004046 [-0.113, 0.1049] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.7039 [0.6254, 0.7823] | 0.434 | — | — | 0.1675 | 1.135e+06 | 0.565 | 0 | 0.0001607 |  |
| arm_cpu | host_cpu_hw | 0.6607 [0.5859, 0.7356] | 0.4073 | — | — | 0.3526 | 3.559e+06 | 0.0009372 | 1296 | -2.871e-05 |  |
| hwmon_power | host_power_socket | 0.132 [0.117, 0.1471] | 0.08142 | — | — | 0 | 8877 | 2.124e-05 | 0 | -0.0001214 |  |
| ib_mad | host_ib | 0.1237 [0.1064, 0.1409] | 0.07614 | — | — | 0.1875 | 1.369e+04 | 4.763 | 0 | 7.98e-05 |  |
| perf_event | arm_aarch64_imc | 0.002506 [-0.001934, 0.006946] | 0.001574 | — | — | 0 | 1.727e+04 | 2.113e-05 | 4 | -3.54e-05 |  |

Framework (NONE): 0.04623 core-s/h
Interaction residual: -0.04678 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 28.97 | 28.97 | 0.0005453 |
| host_cpu | 63 | 3.13 | 3.13 | 0 |
| host_proc | 63 | 3.032 | 3.07 | 0.03776 |
| lustre_osc | 63 | 2.71 | 2.712 | 0.002087 |
| lustre_llite | 63 | 1.98 | 9.696 | 7.717 |
| host_ib | 63 | 1.634 | 6.995 | 5.361 |
| host_power_socket | 63 | 1.151 | 1.151 | 3.81e-07 |
| host_nfs | 63 | 0.8531 | 0.8531 | 0 |
| host_net | 63 | 0.4032 | 0.4031 | 6e-06 |
| host_numa | 63 | 0.3138 | 0.316 | 0.002351 |
| host_mem | 63 | 0.2199 | 0.2199 | 8.095e-07 |
| host_ps | 63 | 0.2116 | 0.2115 | 3.81e-07 |
| lustre_mdc | 63 | 0.1812 | 0.1811 | 5.333e-06 |
| host_vm | 63 | 0.1513 | 0.1513 | 1.292e-05 |
| host_roofline_peak | 63 | 0.1466 | 0.2094 | 0.06287 |
| beegfs_client | 63 | 0.09569 | 0.09553 | 1.476e-06 |
| host_block | 63 | 0.0954 | 0.09535 | 1.397e-06 |
| host_tmpfs | 63 | 0.09308 | 0.0929 | 0 |
| host_sysv_shm | 63 | 0.03382 | 0.03424 | 0.0004728 |
| host_vfs | 63 | 0.03039 | 0.03039 | 1.04e-05 |
| arm_aarch64_imc | 63 | 0.02691 | 0.02778 | 0.0008702 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group arm_cpu on 144 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.0000 | 14,199 | 98.6 | 0.00 |
| ALL - NONE | 6 | 0.0000 | 14,279 | 99.2 | 0.00 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 488056 | hpcperfstatsd | 1.66 | 0.16 | 1.5 | 6.306 | 1 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.05122 | 3357/3411/3438 | 6.306 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.05117 | 3357/3411/3438 | 6.356 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.0362 | 3357/3411/3438 | 1.033 |  |
| performance-b1-OFF- | performance | ok | False | 0.0404 | 3357/3411/3438 | — |  |
| performance-b1-SOLO-arm_cpu | performance | ok | False | 0.03543 | 3357/3411/3438 | 1.034 | host_cpu_hw |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.0352 | 3357/3411/3438 | 1.613 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-hwmon_power | performance | ok | False | 0.03487 | 3357/3411/3438 | 1.033 | host_power_socket |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.03231 | 3357/3411/3438 | 5.796 | host_ib |
| performance-b1-SOLO-perf_event | performance | ok | False | 0.03305 | 3357/3411/3438 | 1.033 | arm_aarch64_imc |
| performance-b2-ALL-first | performance | ok | False | 0.05164 | 3357/3411/3438 | 6.327 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.04861 | 3357/3411/3438 | 6.4 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.03488 | 3357/3411/3438 | 1.033 |  |
| performance-b2-OFF- | performance | ok | False | 0.04078 | 3357/3411/3438 | — |  |
| performance-b2-SOLO-arm_cpu | performance | ok | False | 0.04489 | 3357/3411/3438 | 1.034 | host_cpu_hw |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.03375 | 3357/3411/3438 | 1.62 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-hwmon_power | performance | ok | False | 0.0322 | 3357/3411/3438 | 1.033 | host_power_socket |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.04051 | 3357/3411/3438 | 5.797 | host_ib |
| performance-b2-SOLO-perf_event | performance | ok | False | 0.03478 | 3357/3411/3438 | 1.033 | arm_aarch64_imc |
| performance-b3-ALL-first | performance | ok | False | 0.05119 | 3357/3411/3438 | 6.338 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.04549 | 3357/3411/3438 | 6.33 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.04562 | 3357/3411/3438 | 3.775 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.03438 | 3357/3411/3438 | 1.033 |  |
| performance-b3-OFF- | performance | ok | False | 0.03519 | 3357/3411/3438 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.04047 | 3357/3411/3438 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.04671 | 3357/3411/3438 | 6.236 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-arm_cpu | performance | ok | False | 0.04484 | 3357/3411/3438 | 1.034 | host_cpu_hw |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.0353 | 3357/3411/3438 | 1.561 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-hwmon_power | performance | ok | False | 0.0259 | 3357/3411/3438 | 1.033 | host_power_socket |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.03479 | 3357/3411/3438 | 5.796 | host_ib |
| performance-b3-SOLO-perf_event | performance | ok | False | 0.02749 | 3357/3411/3438 | 1.033 | arm_aarch64_imc |
