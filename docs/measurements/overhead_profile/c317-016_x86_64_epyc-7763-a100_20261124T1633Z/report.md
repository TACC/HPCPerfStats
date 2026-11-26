# hpcperfstatsd overhead profile — c317-016 (x86_64, AMD EPYC 7763 64-Core Processor)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 12.38 [11.53, 13.24] /s | 724.1 [721.5, 726.8] core-s/h | 3093 [3093, 3093] MHz | 0.0545 [0.04431, 0.06469] cores |

> baseline_drift — governor `performance` for NONE, ALL: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 724.1 [721.5, 726.8] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 1.738e+04 [1.732e+04, 1.744e+04] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 20.11 [20.04, 20.19] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.1571 [0.1566, 0.1577] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 5.038 [4.847, 5.229] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 719.1 [716.4, 721.7] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 19.68 [19.45, 19.9] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 22.3 [21.01, 23.59] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 24.99 [24.41, 25.57] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 38.05 [22.15, 53.95] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 42.5 [23.82, 61.19] CPU-ms |
| CPU per send (`cost_ms_per_send`) | 36.71 [23.76, 49.66] CPU-ms |
| RSS steady state (`daemon_rss_mib`) | 149 [148, 150] MiB |
| PSS steady state (`daemon_pss_mib`) | 140.1 [139, 141.2] MiB |
| USS steady state (`daemon_uss_mib`) | 133.2 [132, 134.4] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 149.1 [148, 150.1] MiB |
| Threads (`daemon_threads`) | 7 [7, 7] count |
| Open FDs (`daemon_fds`) | 820 [820, 820] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 778 [778, 778] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 12.38 [11.53, 13.24] /s |
| Minor faults (`daemon_minflt_per_s`) | 4.895 [3.991, 5.799] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 39.05 [39.03, 39.06] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 4.653e+06 [4.649e+06, 4.657e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 952.9 [945, 960.7] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 3386 [3383, 3388] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 3.878e+05 [3.874e+05, 3.881e+05] bytes |
| Daemon IPC (`daemon_ipc`) | 0.1834 [0.1814, 0.1854] instructions/cycle |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | 0.06997 [0.0654, 0.07454] ratio |
| Startup wall time to Enabled types (`startup_wall_s`) | 0.2115 [0.1619, 0.2612] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.304 [0.2486, 0.3595] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 148.1 [146.9, 149.3] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 717.2 [708.6, 725.8] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 1.715 [1.699, 1.73] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 1.268e+05 [1.268e+05, 1.269e+05] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 5.758e+04 [5.496e+04, 6.02e+04] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 200.2 [181.8, 218.6] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | 244.6 [-240.2, 729.4] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 24.22 [24.07, 24.37] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 89.07 [44.8, 133.3] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.8616 [0.767, 0.9562] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.014 [1.013, 1.014] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.014 [1.013, 1.015] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 2.562e+05 [-2.509e+05, 7.632e+05] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.3201 [0.3015, 0.3388] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.009756 [-0.02796, 0.04747] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dcgm_gpu | nvidia_gpu | 736.3 [733.1, 739.6] | 1.017 | — | — | 132.2 | 4.384e+04 | 6.328 | 0 | 0.2268 |  |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 1.578 [1.421, 1.735] | 0.00218 | — | — | 0.4327 | 1.283e+06 | 0.3227 | 0 | 0.005372 |  |
| x86_perf | amd_x86_rapl, amd_x86_uncore_df_milan, host_cpu_hw | 0.3578 [0.3067, 0.4089] | 0.0004941 | — | — | 0.5241 | 3.3e+06 | 0.06878 | 778 | 0.004146 |  |
| ib_mad | host_ib | 0.2584 [0.1707, 0.3461] | 0.0003568 | — | — | 0.1908 | 1.514e+04 | 4.763 | 0 | 0 |  |

Framework (NONE): 0.2714 core-s/h
Interaction residual: -14.69 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 16.73 | 16.98 | 0.2443 |
| host_proc | 63 | 7.958 | 8.09 | 0.132 |
| host_cpu | 63 | 5.072 | 5.154 | 0.082 |
| nvidia_gpu | 63 | 3.142 | 11.87 | 8.728 |
| lustre_osc | 63 | 3.013 | 3.059 | 0.04581 |
| host_ib | 63 | 2.771 | 7.591 | 4.819 |
| lustre_llite | 63 | 2.076 | 3.217 | 1.141 |
| host_net | 63 | 1.939 | 1.974 | 0.03507 |
| beegfs_client | 63 | 1.784 | 1.821 | 0.03686 |
| host_nfs | 63 | 0.8421 | 0.8576 | 0.01544 |
| host_numa | 63 | 0.2957 | 0.2985 | 0.002802 |
| host_mem | 63 | 0.2843 | 0.2861 | 0.001881 |
| host_ps | 63 | 0.2769 | 0.2808 | 0.004116 |
| host_block | 63 | 0.2735 | 0.2747 | 0.001263 |
| host_vm | 63 | 0.2376 | 0.2444 | 0.006759 |
| host_roofline_peak | 63 | 0.2302 | 1.94 | 1.71 |
| lustre_mdc | 63 | 0.2288 | 0.2315 | 0.002745 |
| host_tmpfs | 63 | 0.16 | 0.1615 | 0.00163 |
| amd_x86_uncore_df_milan | 63 | 0.09711 | 0.1007 | 0.003607 |
| host_vfs | 63 | 0.06446 | 0.06548 | 0.001039 |
| amd_x86_rapl | 63 | 0.06215 | 0.07293 | 0.01078 |
| host_sysv_shm | 63 | 0.04495 | 0.04535 | 0.0006491 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 128 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 1.5273 | 126,802 | 990.6 | 12.04 |
| ALL - NONE | 6 | 1.4831 | 126,833 | 990.9 | 11.69 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 2887890 | hpcperfstatsd | 720.4 | 4.563 | 715.8 | 2.725 | 1 |
| 2887878 | hpcperfstatsd | 2.636 | 0.1801 | 2.462 | 6.143 | 1 |
| 2887889 | cache_mgr_main | 0.2648 | 0.02001 | 0.2402 | 2.326 | 0.1625 |
| 2887888 | cache_mgr_event | 0.1023 | 0 | 0.08006 | 1 | 1 |
| 2887886 | hpcperfstatsd | 0.003113 | 0 | 0 | 0.01612 | 0.02589 |
| 2887887 | hpcperfstatsd | 0.003074 | 0 | 0 | 0.01612 | 0.02589 |
| 2887883 | cuda000c5400316 | 0 | 0 | 0 | 0 | 0 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 1.773 | 2976/3093/3102 | 12.23 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 1.768 | 2984/3093/3101 | 11.59 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.06409 | 1432/1794/3518 | 1.033 |  |
| performance-b1-OFF- | performance | ok | False | 0.04968 | 1429/1794/3518 | — |  |
| performance-b1-SOLO-dcgm_gpu | performance | ok | False | 0.2686 | 1364/1794/3519 | 7.382 | nvidia_gpu |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.04434 | 1426/1794/3516 | 1.346 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.04342 | 1361/1794/3519 | 5.794 | host_ib |
| performance-b1-SOLO-x86_perf | performance | ok | False | 1.581 | 2991/3093/3101 | 1.102 | amd_x86_uncore_df_milan, amd_x86_rapl, host_cpu_hw |
| performance-b2-ALL-first | performance | ok | False | 1.756 | 2998/3093/3097 | 12.59 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 1.778 | 2983/3093/3097 | 11.56 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.04347 | 1384/1794/3519 | 1.033 |  |
| performance-b2-OFF- | performance | ok | False | 0.06954 | 1429/1794/3518 | — |  |
| performance-b2-SOLO-dcgm_gpu | performance | ok | False | 0.2518 | 1389/1794/3519 | 7.166 | nvidia_gpu |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.04537 | 1365/1794/3516 | 1.377 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.04566 | 1428/1794/3518 | 5.798 | host_ib |
| performance-b2-SOLO-x86_perf | performance | ok | False | 1.577 | 2990/3093/3099 | 1.101 | amd_x86_uncore_df_milan, amd_x86_rapl, host_cpu_hw |
| performance-b3-ALL-first | performance | ok | False | 1.76 | 2978/3093/3101 | 13.06 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 1.78 | 2980/3093/3097 | 13.27 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 1.768 | 2975/3093/3101 | 9.126 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.04362 | 1430/1794/3518 | 1.033 |  |
| performance-b3-OFF- | performance | ok | False | 0.04916 | 1373/1794/3516 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.04961 | 1372/1794/3519 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 1.77 | 1628/3093/3097 | 12.49 | amd_x86_uncore_df_milan, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-dcgm_gpu | performance | ok | False | 0.2515 | 1431/1794/3519 | 7.534 | nvidia_gpu |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.06416 | 1395/1794/3519 | 1.345 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.04349 | 1430/1794/3517 | 5.797 | host_ib |
| performance-b3-SOLO-x86_perf | performance | ok | False | 1.728 | 3000/3093/3101 | 1.103 | amd_x86_uncore_df_milan, amd_x86_rapl, host_cpu_hw |
