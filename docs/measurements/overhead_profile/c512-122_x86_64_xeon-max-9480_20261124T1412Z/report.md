# hpcperfstatsd overhead profile — c512-122 (x86_64, Intel(R) Xeon(R) CPU Max 9480)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 1.677 [1.647, 1.707] /s | 1.594 [1.464, 1.723] core-s/h | 2437 [2433, 2440] MHz | 0.0445 [0.04364, 0.04535] cores |

> baseline_drift — governor `performance` for OFF: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 1.594 [1.464, 1.723] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 38.25 [35.13, 41.36] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.04427 [0.0411, 0.04743] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0003952 [0.000367, 0.0004235] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.2234 [0.1534, 0.2934] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 1.39 [1.27, 1.511] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.0005906 [-0.000836, 0.002017] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.002389 [-9.768e-05, 0.004875] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 1.374 [1.309, 1.438] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 13.05 [12.49, 13.61] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 13.62 [13.13, 14.12] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 3.875 [3.439, 4.31] MiB |
| PSS steady state (`daemon_pss_mib`) | 1.742 [1.306, 2.178] MiB |
| USS steady state (`daemon_uss_mib`) | 1.678 [1.242, 2.114] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 4.762 [3.02, 6.504] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 1820 [1820, 1820] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1810 [1810, 1810] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 1.677 [1.647, 1.707] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.3051 [0.03406, 0.5761] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 69.87 [69.29, 70.46] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 4.452e+06 [4.447e+06, 4.458e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 952.3 [944, 960.5] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 3249 [3243, 3255] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 3.71e+05 [3.706e+05, 3.715e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 15.33 [-0.03803, 30.7] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.01773 [0.007519, 0.02794] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 3.478 [3.056, 3.9] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 1.387 [1.105, 1.668] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.8877 [0.8846, 0.8907] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 1.102e+05 [1.102e+05, 1.103e+05] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 1.246e+04 [1.171e+04, 1.32e+04] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 100.6 [61.05, 140.2] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -36.99 [-144.1, 70.08] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 5.339 [4.383, 6.294] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 81.93 [35.37, 128.5] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.3955 [0.3874, 0.4037] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.006 [1.006, 1.007] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.004 [1.003, 1.005] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 2.306e+04 [1.578e+04, 3.034e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.03272 [0.0305, 0.03493] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.004481 [-0.00302, 0.01198] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| x86_perf | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr | 0.7793 [0.6887, 0.87] | 0.4898 | — | — | 1.497 | -17.25 | 0.0001869 | 1810 | 0.001415 |  |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.7533 [0.667, 0.8396] | 0.4734 | — | — | 0.9687 | 0.01161 | 0.308 | 0 | 0 |  |
| ib_mad | host_opa | 0.06522 [0.01049, 0.12] | 0.04157 | — | — | 1.426 | 0.0006641 | 0.3349 | 0 | 0.001321 |  |

Framework (NONE): 0.0341 core-s/h
Interaction residual: -0.03835 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 32.68 | 32.96 | 0.2856 |
| host_proc | 63 | 3.257 | 3.292 | 0.03482 |
| host_cpu | 63 | 3.079 | 3.11 | 0.03042 |
| lustre_osc | 63 | 2.987 | 3.016 | 0.02862 |
| intel_x86_uncore_imc_spr | 63 | 2.076 | 2.094 | 0.01773 |
| lustre_llite | 63 | 1.538 | 2.153 | 0.6145 |
| host_net | 63 | 1.202 | 1.213 | 0.01171 |
| host_nfs | 63 | 1.124 | 1.134 | 0.01059 |
| host_opa | 63 | 0.2995 | 0.4656 | 0.1661 |
| host_ps | 63 | 0.2914 | 0.3005 | 0.009177 |
| host_mem | 63 | 0.243 | 0.2446 | 0.001658 |
| host_numa | 63 | 0.2113 | 0.2169 | 0.005612 |
| host_roofline_peak | 63 | 0.1844 | 0.2666 | 0.08233 |
| host_vm | 63 | 0.1829 | 0.1834 | 0.0005616 |
| lustre_mdc | 63 | 0.1738 | 0.174 | 0.0001445 |
| host_tmpfs | 63 | 0.1211 | 0.1208 | 2.032e-06 |
| beegfs_client | 63 | 0.103 | 0.1032 | 0.0002621 |
| host_block | 63 | 0.06715 | 0.06714 | 3.159e-06 |
| host_vfs | 63 | 0.04412 | 0.04412 | 4.419e-05 |
| host_sysv_shm | 63 | 0.03768 | 0.03781 | 0.0002918 |
| intel_x86_rapl | 63 | 0.03548 | 0.03545 | 5.556e-07 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 112 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.8679 | 110,217 | 984.1 | 7.87 |
| ALL - NONE | 6 | 0.8667 | 110,190 | 983.8 | 7.87 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 258971 | hpcperfstatsd | 1.733 | 0.2001 | 1.541 | 1.717 | 0.8833 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.9339 | 2086/2437/2603 | 1.717 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.9345 | 2055/2441/2604 | 1.674 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.03893 | 2086/3500/3500 | 0.9583 |  |
| performance-b1-OFF- | performance | ok | False | 0.04484 | 2089/3500/3500 | — |  |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.03893 | 2060/3500/3500 | 1.271 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.03793 | 2116/3500/3500 | 1.296 | host_opa |
| performance-b1-SOLO-x86_perf | performance | ok | False | 0.9324 | 2126/2445/3939 | 0.9628 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
| performance-b2-ALL-first | performance | ok | False | 0.9288 | 2091/2436/2604 | 1.679 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.9312 | 2051/2434/2602 | 1.662 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.03793 | 2078/3500/3500 | 0.9633 |  |
| performance-b2-OFF- | performance | ok | False | 0.04445 | 2068/3500/3500 | — |  |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.03873 | 2090/3500/3500 | 1.257 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.0378 | 2060/3500/3500 | 1.297 | host_opa |
| performance-b2-SOLO-x86_perf | performance | ok | False | 0.93 | 2107/2434/2602 | 0.96 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
| performance-b3-ALL-first | performance | ok | False | 0.9324 | 2090/2440/2604 | 1.657 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.932 | 2106/2434/2603 | 1.673 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.9324 | 2066/2436/2602 | 1.274 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.03932 | 2097/3500/3500 | 0.9633 |  |
| performance-b3-OFF- | performance | ok | False | 0.04521 | 2118/3500/3500 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.04349 | 2060/3500/3500 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.9352 | 2138/2439/2602 | 1.68 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.03726 | 2066/3500/3500 | 1.282 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.03849 | 2055/3500/3500 | 1.297 | host_opa |
| performance-b3-SOLO-x86_perf | performance | ok | False | 0.9369 | 2108/2437/2604 | 0.9628 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_spr |
