# hpcperfstatsd overhead profile — c573-002 (x86_64, AMD EPYC 9555 64-Core Processor)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 1.707 [1.34, 2.074] /s | 2.763 [2.257, 3.269] core-s/h | 2301 [2301, 2301] MHz | 0.03533 [0.03513, 0.03552] cores |

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 2.763 [2.257, 3.269] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 66.31 [54.17, 78.45] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.07675 [0.06269, 0.0908] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0005996 [0.0004898, 0.0007094] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.08671 [0.0667, 0.1067] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 2.671 [2.181, 3.161] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.0004204 [0.0002793, 0.0005615] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.00244 [0.001569, 0.003311] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 2.788 [2.691, 2.885] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 25.41 [25.31, 25.51] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 25.76 [25.11, 26.41] CPU-ms |
| CPU per send (`cost_ms_per_send`) | 26.59 CPU-ms |
| RSS steady state (`daemon_rss_mib`) | 29.86 [28.97, 30.74] MiB |
| PSS steady state (`daemon_pss_mib`) | 25.47 [24.59, 26.35] MiB |
| USS steady state (`daemon_uss_mib`) | 23.11 [22.23, 23.99] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 30.97 [29.67, 32.28] MiB |
| Threads (`daemon_threads`) | 2 [2, 2] count |
| Open FDs (`daemon_fds`) | 1119 [1119, 1119] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1074 [1074, 1074] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 1.707 [1.34, 2.074] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.4343 [0.1897, 0.6789] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 41.44 [33.84, 49.03] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 4.824e+06 [2.192e+06, 7.457e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.4 [960.2, 960.7] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 3536 [1793, 5279] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 4.02e+05 [1.826e+05, 6.214e+05] bytes |
| Daemon IPC (`daemon_ipc`) | 0.3876 [0.3308, 0.4444] instructions/cycle |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | 0.2079 [0.1373, 0.2784] ratio |
| Startup wall time to Enabled types (`startup_wall_s`) | 15.63 [0.3548, 30.9] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.2626 [0.2381, 0.2872] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 29.81 [27.44, 32.18] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 3.057 [2.382, 3.731] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.3889 [0.3871, 0.3907] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 1.281e+05 [1.28e+05, 1.282e+05] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | -50.36 [-75.79, -24.93] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 92.41 [62.41, 122.4] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -417.8 [-1636, 800] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | -5.104 [-37.69, 27.48] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 75.1 [47.86, 102.3] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.2971 [0.2725, 0.3216] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.01 [1.01, 1.011] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.011 [1.01, 1.012] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 2.263e+04 [1.925e+04, 2.601e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.1897 [0.1689, 0.2104] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.005966 [-0.02137, 0.0333] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dcgm_gpu | nvidia_gpu | 2.201 [1.941, 2.461] | 0.8009 | — | — | 22.65 | -0.03023 | -0.0007482 | 0 | 0.2296 |  |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.5027 [0.4417, 0.5636] | 0.1833 | — | — | 1.668 | 0.03003 | 0.3494 | 0 | 0.001111 |  |
| x86_perf | amd_x86_rapl, amd_x86_uncore_df_turin, host_cpu_hw | 0.226 [0.197, 0.255] | 0.08219 | — | — | 1.195 | -31.49 | 0.06057 | 1074 | 0 |  |
| ib_mad | host_ib, host_opa | 0.02017 [0.01546, 0.02487] | 0.007307 | — | — | 0.9173 | -46.15 | 0.3723 | 0 | 0.0006413 |  |

Framework (NONE): 0.03761 core-s/h
Interaction residual: -0.2246 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| nvidia_gpu | 63 | 21.75 | 22.11 | 0.362 |
| host_cpu_hw | 63 | 8.096 | 8.132 | 0.03638 |
| host_proc | 63 | 2.233 | 2.246 | 0.01367 |
| host_cpu | 63 | 2.15 | 2.164 | 0.01381 |
| lustre_osc | 63 | 1.133 | 1.139 | 0.006023 |
| lustre_llite | 63 | 0.9967 | 2.291 | 1.294 |
| host_numa | 63 | 0.5318 | 0.5367 | 0.004932 |
| host_nfs | 63 | 0.4545 | 0.4557 | 0.001212 |
| host_mem | 63 | 0.3884 | 0.3909 | 0.002517 |
| host_net | 63 | 0.3847 | 0.3868 | 0.002143 |
| host_ps | 63 | 0.1674 | 0.1679 | 0.0005279 |
| amd_x86_uncore_df_turin | 63 | 0.1389 | 0.1397 | 0.0007661 |
| host_block | 63 | 0.1321 | 0.1321 | 2.429e-06 |
| host_opa | 63 | 0.13 | 0.1311 | 0.001056 |
| host_vm | 63 | 0.1213 | 0.122 | 0.0007393 |
| beegfs_client | 63 | 0.1108 | 0.1108 | 3.175e-08 |
| host_roofline_peak | 63 | 0.09086 | 5.782 | 5.691 |
| host_ib | 63 | 0.08469 | 0.5157 | 0.431 |
| lustre_mdc | 63 | 0.07283 | 0.0733 | 0.0004711 |
| host_tmpfs | 63 | 0.04968 | 0.04974 | 7.64e-05 |
| amd_x86_rapl | 63 | 0.02772 | 0.03555 | 0.007825 |
| host_vfs | 63 | 0.02207 | 0.02207 | 1.413e-06 |
| host_sysv_shm | 63 | 0.01599 | 0.01614 | 0.0001555 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 128 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.3879 | 128,093 | 1,000.7 | 3.03 |
| ALL - NONE | 6 | 0.3890 | 128,038 | 1,000.3 | 3.04 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 216810 | hpcperfstatsd | 3.078 | 0.1001 | 2.982 | 1.974 | 0.9961 |
| 216815 | cuda0010f40043e | 0 | 0 | 0 | 0 | 0 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.4242 | 2301/2301/4450 | 1.974 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.4262 | 2301/2301/4452 | 1.39 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.0284 | 2301/2301/4466 | 0.9589 |  |
| performance-b1-OFF- | performance | ok | False | 0.03511 | 2301/2301/4481 | — |  |
| performance-b1-SOLO-dcgm_gpu | performance | ok | False | 0.03016 | 2301/2301/4478 | 0.9561 | nvidia_gpu |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.02956 | 2301/2301/4478 | 1.369 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.02842 | 2301/2301/4475 | 1.316 | host_ib, host_opa |
| performance-b1-SOLO-x86_perf | performance | ok | False | 0.4211 | 2301/2301/4453 | 1.023 | amd_x86_uncore_df_turin, amd_x86_rapl, host_cpu_hw |
| performance-b2-ALL-first | performance | ok | False | 0.4225 | 2301/2301/4453 | 1.24 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.4252 | 2301/2301/4447 | 1.879 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.02937 | 2301/2301/4490 | 0.96 |  |
| performance-b2-OFF- | performance | ok | False | 0.03526 | 2301/2301/4475 | — |  |
| performance-b2-SOLO-dcgm_gpu | performance | ok | False | 0.03009 | 2301/2301/4484 | 0.9594 | nvidia_gpu |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.02948 | 2301/2301/4479 | 1.273 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.02924 | 2301/2301/4472 | 1.364 | host_ib, host_opa |
| performance-b2-SOLO-x86_perf | performance | ok | False | 0.4226 | 2301/2301/4453 | 1.011 | amd_x86_uncore_df_turin, amd_x86_rapl, host_cpu_hw |
| performance-b3-ALL-first | performance | ok | False | 0.4235 | 2301/2301/4454 | 1.891 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.4237 | 2301/2301/4453 | 1.867 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.4222 | 2301/2301/4451 | 1.372 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.0292 | 2301/2301/4483 | 0.9628 |  |
| performance-b3-OFF- | performance | ok | False | 0.03544 | 2301/2301/4487 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.0355 | 2301/2301/4475 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.4273 | 2301/2301/4450 | 1.804 | amd_x86_uncore_df_turin, amd_x86_rapl, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-dcgm_gpu | performance | ok | False | 0.02989 | 2301/2301/4474 | 0.9639 | nvidia_gpu |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.02904 | 2301/2301/4497 | 1.287 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.02857 | 2301/2301/4483 | 1.319 | host_ib, host_opa |
| performance-b3-SOLO-x86_perf | performance | ok | False | 0.4215 | 2301/2301/4451 | 1.03 | amd_x86_uncore_df_turin, amd_x86_rapl, host_cpu_hw |
