# hpcperfstatsd overhead profile — c313-031 (x86_64, AMD EPYC 7763 64-Core Processor)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| (not set) | 6.143 [6.099, 6.188] /s | 2.666 [2.458, 2.874] core-s/h | NOT_MEASURED (no scaling_cur_freq in ALL legs) | 0.06067 [0.0596, 0.06174] cores |

> baseline_drift for NONE: comparisons involving them are not citable.

## Headline metrics

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 2.666 [2.458, 2.874] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 63.98 [59, 68.97] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.07405 [0.07151, 0.0766] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0005786 [0.0005586, 0.0005985] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.2034 [0.1634, 0.2435] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 2.458 [2.378, 2.538] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.002344 [0.001964, 0.002724] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.0401 [-0.05081, 0.131] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 2.654 [2.416, 2.891] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 20.87 [20.19, 21.55] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 25.15 [22.95, 27.35] CPU-ms |
| CPU per send (`cost_ms_per_send`) | 1.192 CPU-ms |
| RSS steady state (`daemon_rss_mib`) | 3.868 [3.809, 3.927] MiB |
| PSS steady state (`daemon_pss_mib`) | 1.763 [1.728, 1.797] MiB |
| USS steady state (`daemon_uss_mib`) | 1.65 [1.62, 1.679] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 4.271 [4.216, 4.326] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 790 [790, 790] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 778 [778, 778] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 6.143 [6.099, 6.188] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.4788 [0.4354, 0.5221] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 39.1 [39.1, 39.1] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 4.802e+06 [4.798e+06, 4.805e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.5 [960.5, 960.5] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 3488 [3486, 3490] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 4.002e+05 [3.999e+05, 4.005e+05] bytes |
| Daemon IPC (`daemon_ipc`) | 0.5385 [0.4385, 0.6385] instructions/cycle |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | 0.1768 [0.1368, 0.2168] ratio |
| Startup wall time to Enabled types (`startup_wall_s`) | 0.1364 [0.1276, 0.1452] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.02506 [0.01819, 0.03193] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 3.555 [3.503, 3.608] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 2.208 [1.745, 2.671] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 1.18 [1.177, 1.183] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 1.277e+05 [1.277e+05, 1.277e+05] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 65.53 [41.93, 89.13] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 58.94 [47.52, 70.36] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | 112.2 [-1072, 1296] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | -11.11 [-11.22, -11] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 89.2 [15.04, 163.4] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.7705 [0.7272, 0.8138] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.013 [1.012, 1.013] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.013 [1.012, 1.015] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 2.448e+05 [-4.55e+05, 9.446e+05] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.8138 [0.792, 0.8356] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | -0.00203 [-0.03083, 0.02677] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block)

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 1.678 [1.485, 1.87] | 0.6294 | — | — | 0.4238 | 1.464e+06 | 0.3272 | 0 | 0.01049 |  |
| x86_perf | amd_x86_rapl, amd_x86_uncore_df_milan, host_cpu_hw | 0.5529 [0.4738, 0.6321] | 0.2071 | — | — | 0.5418 | 3.293e+06 | 0.07263 | 778 | 0 |  |
| ib_mad | host_ib | 0.277 [0.2416, 0.3123] | 0.1039 | — | — | 0.19 | 1.982e+04 | 4.763 | 0 | 0 |  |

Framework (NONE): 0.1575 core-s/h
Interaction residual: 0.0008571 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 16.42 | 16.67 | 0.2467 |
| host_proc | 63 | 8.668 | 8.818 | 0.1493 |
| host_cpu | 63 | 4.812 | 4.89 | 0.07753 |
| lustre_osc | 63 | 2.86 | 2.905 | 0.04541 |
| lustre_llite | 63 | 2.424 | 3.809 | 1.385 |
| host_ib | 63 | 2.385 | 6.698 | 4.312 |
| host_net | 63 | 1.692 | 1.72 | 0.02823 |
| beegfs_client | 63 | 1.47 | 1.546 | 0.07599 |
| host_nfs | 63 | 0.848 | 0.8607 | 0.01272 |
| lustre_mdc | 63 | 0.447 | 0.4543 | 0.00728 |
| host_numa | 63 | 0.2739 | 0.2774 | 0.003548 |
| host_mem | 63 | 0.2673 | 0.2731 | 0.005813 |
| host_block | 63 | 0.2615 | 0.2654 | 0.003861 |
| host_ps | 63 | 0.2514 | 0.2559 | 0.004588 |
| host_roofline_peak | 63 | 0.2416 | 0.5175 | 0.2761 |
| host_vm | 63 | 0.2176 | 0.2215 | 0.003923 |
| host_tmpfs | 63 | 0.1469 | 0.1493 | 0.002515 |
| amd_x86_uncore_df_milan | 63 | 0.08218 | 0.08642 | 0.004237 |
| amd_x86_rapl | 63 | 0.07443 | 0.09721 | 0.02278 |
| host_vfs | 63 | 0.06126 | 0.06226 | 0.00101 |
| host_sysv_shm | 63 | 0.04069 | 0.04187 | 0.001333 |

## Daemon threads (hottest, first usable ALL leg)

Leg b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 961481 | hpcperfstatsd | 2.685 | 0.2201 | 2.461 | 6.187 | 0.9983 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| b1-ALL-first | — | ok | False | 1.238 | — | 6.187 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b1-ALL-second | — | ok | False | 1.24 | — | 6.177 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b1-NONE- | — | ok | False | 0.05375 | — | 1.033 |  |
| b1-OFF- | — | ok | False | 0.06055 | — | — |  |
| b1-SOLO-filesystem | — | ok | False | 0.04883 | — | 1.35 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b1-SOLO-ib_mad | — | ok | False | 0.06883 | — | 5.798 | host_ib |
| b1-SOLO-x86_perf | — | ok | False | 1.239 | — | 1.103 | amd_x86_uncore_df_milan, amd_x86_rapl, host_cpu_hw |
| b2-ALL-first | — | ok | False | 1.244 | — | 6.112 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b2-ALL-second | — | ok | False | 1.243 | — | 6.165 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b2-NONE- | — | ok | False | 0.05629 | — | 1.033 |  |
| b2-OFF- | — | ok | False | 0.06041 | — | — |  |
| b2-SOLO-filesystem | — | ok | False | 0.04922 | — | 1.399 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b2-SOLO-ib_mad | — | ok | False | 0.04713 | — | 5.795 | host_ib |
| b2-SOLO-x86_perf | — | ok | False | 1.241 | — | 1.101 | amd_x86_uncore_df_milan, amd_x86_rapl, host_cpu_hw |
| b3-ALL-first | — | ok | False | 1.24 | — | 6.098 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b3-ALL-second | — | ok | False | 1.239 | — | 6.121 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b3-CADENCE-60.0 | — | ok | False | 1.257 | — | 3.564 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b3-NONE- | — | ok | False | 0.0488 | — | 1.033 |  |
| b3-OFF- | — | ok | False | 0.06187 | — | — |  |
| b3-OFF-end | — | ok | False | 0.05985 | — | — |  |
| b3-PROFILE- | — | ok | False | 1.24 | — | 6.268 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b3-SOLO-filesystem | — | ok | False | 0.06938 | — | 1.333 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| b3-SOLO-ib_mad | — | ok | False | 0.04878 | — | 5.796 | host_ib |
| b3-SOLO-x86_perf | — | ok | False | 1.261 | — | 1.115 | amd_x86_uncore_df_milan, amd_x86_rapl, host_cpu_hw |
