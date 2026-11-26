# hpcperfstatsd overhead profile — c318-005 (x86_64, AMD EPYC 9454 48-Core Processor)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 6.196 [6.174, 6.217] /s | 1.58 [1.467, 1.692] core-s/h | 3798 [3798, 3798] MHz | 0.03805 [0.03723, 0.03888] cores |

> baseline_drift — governor `performance` for NONE: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 1.58 [1.467, 1.692] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 37.92 [35.22, 40.62] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.04389 [0.04312, 0.04465] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0004571 [0.0004492, 0.0004651] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1067 [0.07674, 0.1367] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 1.464 [1.414, 1.515] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.002133 [0.001731, 0.002534] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.006649 [0.00506, 0.008238] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 1.152 [1.097, 1.206] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 9.81 [9.175, 10.44] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 11.35 [10.65, 12.05] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 4.011 [3.897, 4.125] MiB |
| PSS steady state (`daemon_pss_mib`) | 1.756 [1.671, 1.841] MiB |
| USS steady state (`daemon_uss_mib`) | 1.665 [1.58, 1.749] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 4.202 [4.058, 4.347] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 614 [614, 614] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 602 [602, 602] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 6.196 [6.174, 6.217] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.2041 [0.1265, 0.2817] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 32.13 [32.12, 32.13] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 3.559e+06 [3.557e+06, 3.56e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.7 [960.4, 960.9] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 2634 [2631, 2637] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 2.966e+05 [2.964e+05, 2.967e+05] bytes |
| Daemon IPC (`daemon_ipc`) | 0.7113 [0.3062, 1.116] instructions/cycle |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | 0.164 [0.07832, 0.2497] ratio |
| Startup wall time to Enabled types (`startup_wall_s`) | 0.1124 [0.1008, 0.1239] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.01787 [0.0122, 0.02353] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 3.37 [3.226, 3.515] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 1.637 [1.301, 1.972] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.7447 [0.7321, 0.7573] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 9.473e+04 [9.471e+04, 9.475e+04] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 4.628e+04 [4.498e+04, 4.757e+04] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 161.1 [129.7, 192.6] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | 65.23 [-776.4, 906.8] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 18.48 [18.26, 18.7] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 65.9 [29.08, 102.7] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.4941 [0.4855, 0.5026] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.014 [1.013, 1.014] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.024 [1.02, 1.028] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 1.311e+05 [-2.135e+05, 4.758e+05] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.2587 [0.2427, 0.2746] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.0006374 [-0.006238, 0.007513] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 1.011 [0.8969, 1.125] | 0.6399 | — | — | 0.3564 | 9.987e+05 | 0.3293 | 0 | 0.009907 |  |
| ib_mad | host_ib | 0.2331 [0.2012, 0.2649] | 0.1474 | — | — | 0.196 | 1.393e+04 | 4.773 | 0 | 0.0003222 |  |
| x86_perf | amd_x86_rapl, amd_x86_uncore_df_genoa, host_cpu_hw | 0.1439 [0.09867, 0.189] | 0.09092 | — | — | 0.4376 | 2.538e+06 | 0.07874 | 602 | 0.003272 |  |

Framework (NONE): 0.2307 core-s/h
Interaction residual: -0.03877 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 8.071 | 8.144 | 0.07244 |
| host_proc | 63 | 4.799 | 4.851 | 0.05129 |
| host_cpu | 63 | 2.719 | 2.748 | 0.02941 |
| lustre_osc | 63 | 1.912 | 1.93 | 0.01773 |
| host_ib | 63 | 1.734 | 6.263 | 4.529 |
| lustre_llite | 63 | 1.403 | 2.203 | 0.7996 |
| beegfs_client | 63 | 1.193 | 1.218 | 0.02537 |
| host_net | 63 | 0.9354 | 0.9411 | 0.005701 |
| host_nfs | 63 | 0.5597 | 0.5683 | 0.008518 |
| host_block | 63 | 0.2834 | 0.2834 | 6.794e-06 |
| host_mem | 63 | 0.1888 | 0.1936 | 0.004754 |
| amd_x86_uncore_df_genoa | 63 | 0.177 | 0.1797 | 0.002779 |
| host_numa | 63 | 0.1754 | 0.176 | 0.0005598 |
| host_ps | 63 | 0.1738 | 0.1739 | 0.0001483 |
| host_vm | 63 | 0.1663 | 0.1709 | 0.004582 |
| lustre_mdc | 63 | 0.1615 | 0.1629 | 0.001358 |
| host_roofline_peak | 63 | 0.1366 | 0.4259 | 0.2893 |
| host_tmpfs | 63 | 0.1289 | 0.1296 | 0.0007205 |
| host_vfs | 63 | 0.04313 | 0.04344 | 0.0003149 |
| amd_x86_rapl | 63 | 0.042 | 0.05506 | 0.01306 |
| host_sysv_shm | 63 | 0.02759 | 0.02754 | 0 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 1549241 | hpcperfstatsd | 1.56 | 0.14 | 1.42 | 6.206 | 1 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.7744 | 3605/3798/3805 | 6.206 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.7842 | 3367/3798/3808 | 6.194 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.03909 | 3309/3797/3951 | 1 |  |
| performance-b1-OFF- | performance | ok | False | 0.03828 | 3511/3797/3981 | — |  |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.0384 | 3254/3797/3945 | 1.35 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.03849 | 3406/3797/3973 | 5.796 | host_ib |
| performance-b1-SOLO-x86_perf | performance | ok | False | 0.7847 | 3547/3798/3807 | 1.102 | amd_x86_uncore_df_genoa, amd_x86_rapl, host_cpu_hw |
| performance-b2-ALL-first | performance | ok | False | 0.7791 | 3403/3798/3805 | 6.192 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.7818 | 3600/3798/3807 | 6.166 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.03903 | 3222/3797/3969 | 1.033 |  |
| performance-b2-OFF- | performance | ok | False | 0.03735 | 3505/3797/3972 | — |  |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.03928 | 3231/3797/3966 | 1.367 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.03833 | 3301/3797/3968 | 5.795 | host_ib |
| performance-b2-SOLO-x86_perf | performance | ok | False | 0.7826 | 3389/3798/3805 | 1.101 | amd_x86_uncore_df_genoa, amd_x86_rapl, host_cpu_hw |
| performance-b3-ALL-first | performance | ok | False | 0.7774 | 3421/3798/3807 | 6.209 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.7997 | 3274/3798/3806 | 6.207 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.7988 | 3466/3798/3807 | 3.61 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.03887 | 3217/3797/3972 | 1.034 |  |
| performance-b3-OFF- | performance | ok | False | 0.03853 | 3361/3797/3984 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.03805 | 3421/3797/3972 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.7851 | 3300/3798/3850 | 6.16 | amd_x86_uncore_df_genoa, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.03917 | 3248/3797/3949 | 1.338 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.05497 | 3369/3797/3962 | 5.796 | host_ib |
| performance-b3-SOLO-x86_perf | performance | ok | False | 0.7753 | 3506/3798/3807 | 1.101 | amd_x86_uncore_df_genoa, amd_x86_rapl, host_cpu_hw |
