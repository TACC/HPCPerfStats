# hpcperfstatsd overhead profile — c561-009 (x86_64, Intel(R) Xeon(R) Platinum 8468)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 6.189 [6.089, 6.288] /s | 1.568 [1.442, 1.695] core-s/h | 2875 [2872, 2878] MHz | 0.03304 [0.03273, 0.03336] cores |

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 1.568 [1.442, 1.695] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 37.63 [34.6, 40.67] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.04356 [0.0424, 0.04472] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0004537 [0.0004416, 0.0004658] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1734 [0.1534, 0.1934] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 1.401 [1.371, 1.431] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.000188 [-0.000319, 0.000695] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.03352 [-0.06263, 0.1297] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 1.352 [1.117, 1.587] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 12.87 [12.64, 13.1] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 13.38 [12.97, 13.79] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 29.3 [29.13, 29.48] MiB |
| PSS steady state (`daemon_pss_mib`) | 24.85 [24.67, 25.02] MiB |
| USS steady state (`daemon_uss_mib`) | 22.47 [22.3, 22.65] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 29.95 [28.82, 31.07] MiB |
| Threads (`daemon_threads`) | 2 [2, 2] count |
| Open FDs (`daemon_fds`) | 1507 [1507, 1507] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1476 [1476, 1476] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 6.189 [6.089, 6.288] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.4361 [0.3199, 0.5523] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 62.08 [62.08, 62.08] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 3.921e+06 [3.917e+06, 3.924e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.5 [960.5, 960.5] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 2881 [2878, 2884] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 3.267e+05 [3.264e+05, 3.27e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 30.95 [30.78, 31.11] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.3754 [0.3702, 0.3806] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 28.87 [28.77, 28.97] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 1.35 [1.055, 1.645] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.5758 [0.5708, 0.5809] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 9.573e+04 [9.571e+04, 9.576e+04] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 9536 [8981, 1.009e+04] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 93.84 [65.92, 121.8] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -78.07 [-393.2, 237] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 2.852 [2.776, 2.928] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 60.64 [27.55, 93.73] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.323 [0.1964, 0.4497] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 0.9971 [0.9396, 1.055] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1 [0.9599, 1.041] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 7818 [4618, 1.102e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.00994 [0.00899, 0.01089] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.04325 [-0.05855, 0.1451] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| x86_perf | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr | 0.5881 [0.5183, 0.6579] | 0.3751 | — | — | 1.685 | -92.55 | 0.0007411 | 1476 | 0.01734 |  |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.4953 [0.4378, 0.5529] | 0.3158 | — | — | 0.445 | -138.8 | 0.4523 | 0 | 0.004961 |  |
| dcgm_gpu | nvidia_gpu | 0.2731 [0.2375, 0.3087] | 0.1741 | — | — | 21.47 | -46.27 | -0.003527 | 0 | 0.336 |  |
| ib_mad | host_ib, host_opa | 0.1148 [0.1016, 0.128] | 0.07318 | — | — | 0.4022 | -92.55 | 4.716 | 0 | 0.0007249 |  |

Framework (NONE): 0.02481 core-s/h
Interaction residual: 0.07196 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 62

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 62 | 21.99 | 22.39 | 0.3924 |
| nvidia_gpu | 62 | 4.036 | 4.171 | 0.1344 |
| host_proc | 62 | 2.334 | 2.362 | 0.0282 |
| lustre_osc | 62 | 2.217 | 2.24 | 0.02264 |
| host_cpu | 62 | 2.059 | 2.081 | 0.02214 |
| lustre_llite | 62 | 1.347 | 2.174 | 0.8273 |
| host_net | 62 | 1.007 | 1.018 | 0.01123 |
| host_ib | 62 | 0.9833 | 6.073 | 5.09 |
| host_nfs | 62 | 0.846 | 0.8534 | 0.007439 |
| intel_x86_uncore_imc_spr | 62 | 0.4049 | 0.4053 | 0.0004631 |
| host_ps | 62 | 0.2331 | 0.234 | 0.0008575 |
| host_mem | 62 | 0.1996 | 0.2009 | 0.001349 |
| host_opa | 62 | 0.19 | 0.1936 | 0.003675 |
| lustre_mdc | 62 | 0.161 | 0.1633 | 0.002341 |
| host_numa | 62 | 0.1466 | 0.1489 | 0.00237 |
| host_roofline_peak | 62 | 0.1344 | 6.238 | 6.103 |
| host_vm | 62 | 0.1272 | 0.1298 | 0.002567 |
| host_tmpfs | 62 | 0.08724 | 0.08789 | 0.0006645 |
| beegfs_client | 62 | 0.08322 | 0.08333 | 0.0001173 |
| host_block | 62 | 0.06492 | 0.06492 | 3.355e-06 |
| intel_x86_rapl | 62 | 0.05056 | 0.05055 | 8.71e-07 |
| host_sysv_shm | 62 | 0.0279 | 0.02804 | 0.0001515 |
| host_vfs | 62 | 0.02256 | 0.02256 | 3.097e-06 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 96 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.5526 | 95,694 | 996.8 | 5.77 |
| ALL - NONE | 6 | 0.5594 | 95,705 | 996.9 | 5.85 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 2435557 | hpcperfstatsd | 1.59 | 0.1601 | 1.441 | 6.135 | 0.711 |
| 2435562 | cuda001734005ce | 0 | 0 | 0 | 0 | 0 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.6028 | 1239/2877/3100 | 6.135 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.6093 | 1239/2877/3100 | 6.12 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.02582 | 1133/3800/3800 | 0.9633 |  |
| performance-b1-OFF- | performance | ok | False | 0.03267 | 1306/3800/3800 | — |  |
| performance-b1-SOLO-dcgm_gpu | performance | ok | False | 0.02605 | 1151/3800/3800 | 0.96 | nvidia_gpu |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.02594 | 1261/3800/3800 | 1.49 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.02636 | 1118/3800/3800 | 5.657 | host_ib, host_opa |
| performance-b1-SOLO-x86_perf | performance | ok | False | 0.6084 | 1243/2880/3100 | 0.9633 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
| performance-b2-ALL-first | performance | ok | False | 0.613 | 1240/2873/3100 | 6.155 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.6103 | 1241/2878/3100 | 6.302 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.0258 | 1121/3800/3800 | 0.9633 |  |
| performance-b2-OFF- | performance | ok | False | 0.0333 | 1308/3800/3800 | — |  |
| performance-b2-SOLO-dcgm_gpu | performance | ok | False | 0.02709 | 1200/3800/3800 | 0.96 | nvidia_gpu |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.02661 | 1173/3800/3800 | 1.487 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.0267 | 1312/3800/3800 | 5.656 | host_ib, host_opa |
| performance-b2-SOLO-x86_perf | performance | ok | False | 0.6063 | 1241/2879/3100 | 0.9639 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
| performance-b3-ALL-first | performance | ok | False | 0.6088 | 1237/2873/3100 | 6.31 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.6089 | 1239/2872/3100 | 6.111 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.5932 | 1237/2874/3100 | 3.632 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.02576 | 1120/3800/3800 | 0.9633 |  |
| performance-b3-OFF- | performance | ok | False | 0.03298 | 1336/3800/3800 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.03322 | 1137/3800/3800 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.6127 | 1240/2876/3105 | 6.136 | beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-dcgm_gpu | performance | ok | False | 0.02605 | 1188/3800/3800 | 0.9594 | nvidia_gpu |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.02597 | 1138/3800/3800 | 1.269 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.02616 | 1221/3800/3800 | 5.725 | host_ib, host_opa |
| performance-b3-SOLO-x86_perf | performance | ok | False | 0.5894 | 1237/2875/3100 | 0.965 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
