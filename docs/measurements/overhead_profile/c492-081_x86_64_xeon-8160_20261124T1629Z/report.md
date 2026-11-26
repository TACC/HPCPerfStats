# hpcperfstatsd overhead profile — c492-081 (x86_64, Intel(R) Xeon(R) Platinum 8160 CPU @ 2.10GHz)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 1.443 [1.339, 1.546] /s | 0.8232 [0.7604, 0.8859] core-s/h | 1900 [1900, 1900] MHz | 0.02397 [0.02376, 0.02419] cores |

> baseline_drift — governor `performance` for NONE: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 0.8232 [0.7604, 0.8859] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 19.76 [18.25, 21.26] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.02287 [0.0216, 0.02413] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0004764 [0.00045, 0.0005027] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1767 [0.1467, 0.2067] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 0.6501 [0.6201, 0.6801] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.0005304 [-0.0006425, 0.001703] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.002849 [0.001764, 0.003933] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 0.8256 [0.8104, 0.8408] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 6.499 [6.39, 6.607] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 8.161 [8.064, 8.258] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 3.434 [3.293, 3.576] MiB |
| PSS steady state (`daemon_pss_mib`) | 1.306 [1.176, 1.437] MiB |
| USS steady state (`daemon_uss_mib`) | 1.24 [1.11, 1.369] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 3.774 [3.165, 4.383] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 758 [758, 758] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 748 [748, 748] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 1.443 [1.339, 1.546] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.1246 [0.03952, 0.2096] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 32.14 [32.13, 32.15] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 2.048e+06 [2.047e+06, 2.05e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.2 [959.9, 960.5] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 1589 [1587, 1591] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 1.707e+05 [1.706e+05, 1.708e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 15.44 [-0.05304, 30.94] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.01078 [0.006706, 0.01486] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 3.202 [3.079, 3.326] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 0.7994 [0.6269, 0.9719] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 1.004 [0.9999, 1.009] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 4.78e+04 [4.777e+04, 4.783e+04] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 4578 [4286, 4870] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 74.37 [48.93, 99.8] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -75.87 [-267.8, 116] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 3.893 [3.142, 4.645] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 41.98 [13.9, 70.06] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.6602 [0.6326, 0.6877] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.012 [1.012, 1.013] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.013 [1.012, 1.014] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 2.11e+04 [-2.14e+04, 6.36e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 1 [1, 1] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.00742 [-0.03233, 0.04717] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.5658 [0.4983, 0.6332] | 0.6875 | — | — | 0.7305 | 29.02 | 0.3682 | 0 | 0.0002855 |  |
| x86_perf | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx | 0.1554 [0.1206, 0.1902] | 0.1881 | — | — | 0.5749 | 29.02 | -0.001848 | 748 | 0 |  |
| ib_mad | host_opa | 0.01832 [0.00243, 0.0342] | 0.02191 | — | — | 0.6933 | 29.02 | -0.002218 | 0 | 0.0009092 |  |

Framework (NONE): 0.08928 core-s/h
Interaction residual: -0.005577 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 8.7 | 8.793 | 0.09257 |
| lustre_osc | 63 | 1.91 | 1.933 | 0.02265 |
| host_proc | 63 | 1.795 | 1.823 | 0.02766 |
| lustre_llite | 63 | 1.212 | 1.954 | 0.7419 |
| host_net | 63 | 1.179 | 1.192 | 0.01333 |
| host_cpu | 63 | 0.8893 | 0.9045 | 0.01524 |
| host_nfs | 63 | 0.7866 | 0.7953 | 0.008672 |
| intel_x86_uncore_imc_skx | 63 | 0.2581 | 0.2629 | 0.004772 |
| host_mem | 63 | 0.1828 | 0.1854 | 0.002566 |
| beegfs_client | 63 | 0.1827 | 0.1831 | 0.0004738 |
| host_opa | 63 | 0.1671 | 0.1691 | 0.002056 |
| lustre_mdc | 63 | 0.1328 | 0.1362 | 0.00339 |
| host_numa | 63 | 0.1254 | 0.128 | 0.002578 |
| host_vm | 63 | 0.1242 | 0.1251 | 0.0009608 |
| host_ps | 63 | 0.1197 | 0.1228 | 0.00309 |
| host_roofline_peak | 63 | 0.1091 | 0.1843 | 0.07519 |
| host_tmpfs | 63 | 0.1013 | 0.1016 | 0.000436 |
| host_block | 63 | 0.06772 | 0.06757 | 5.66e-05 |
| intel_x86_rapl | 63 | 0.04192 | 0.0432 | 0.001296 |
| host_sysv_shm | 63 | 0.02329 | 0.02327 | 6.667e-07 |
| host_vfs | 63 | 0.02115 | 0.02116 | 1.284e-05 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 48 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.9911 | 47,760 | 995.0 | 20.75 |
| ALL - NONE | 6 | 0.9941 | 47,763 | 995.1 | 20.81 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 395957 | hpcperfstatsd | 0.8862 | 0.22 | 0.68 | 1.422 | 0.9928 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 1.028 | 999.6/1900/2801 | 1.422 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 1.026 | 999.6/1900/2801 | 1.604 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.02022 | 998.8/3700/3700 | 0.9633 |  |
| performance-b1-OFF- | performance | ok | False | 0.02368 | 999.3/3700/3700 | — |  |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.02049 | 999.2/3700/3700 | 1.271 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.02062 | 998.7/3700/3700 | 0.96 | host_opa |
| performance-b1-SOLO-x86_perf | performance | ok | False | 1.025 | 999.6/1900/2801 | 0.9633 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx |
| performance-b2-ALL-first | performance | ok | False | 1.031 | 999.6/1900/2801 | 1.398 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 1.028 | 999.6/1900/2801 | 1.433 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.02083 | 999/3700/3700 | 0.9628 |  |
| performance-b2-OFF- | performance | ok | False | 0.02403 | 999.1/3700/3700 | — |  |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.02319 | 998.7/3700/3700 | 1.363 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.02016 | 999.2/3700/3700 | 0.9589 | host_opa |
| performance-b2-SOLO-x86_perf | performance | ok | False | 1.032 | 999.6/1900/2801 | 0.96 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx |
| performance-b3-ALL-first | performance | ok | False | 1.033 | 999.6/1900/2801 | 1.402 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 1.024 | 999.6/1900/2801 | 1.396 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 1.015 | 999.6/1900/2801 | 1.236 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.02084 | 999.1/3700/3700 | 0.9628 |  |
| performance-b3-OFF- | performance | ok | False | 0.02408 | 999/3700/3700 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.0241 | 999.2/3700/3700 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 1.03 | 999.6/1900/2801 | 1.623 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.02159 | 999/3700/3700 | 1.359 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.0209 | 999.2/3700/3700 | 0.9633 | host_opa |
| performance-b3-SOLO-x86_perf | performance | ok | False | 1.019 | 999.6/1900/2801 | 0.96 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_skx |
