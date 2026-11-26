# hpcperfstatsd overhead profile — c460-220 (x86_64, Intel(R) Xeon(R) Platinum 8380 CPU @ 2.30GHz)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 1.409 [1.387, 1.431] /s | 0.898 [0.8274, 0.9686] core-s/h | 1898 [1898, 1898] MHz | 0.03001 [0.02921, 0.0308] cores |

> baseline_drift — governor `performance` for OFF: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 0.898 [0.8274, 0.9686] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 21.55 [19.86, 23.25] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.02494 [0.02442, 0.02547] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0003118 [0.0003053, 0.0003183] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1367 [0.1167, 0.1568] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 0.7604 [0.7204, 0.8005] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.0001992 [6.037e-05, 0.000338] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.002965 [0.002414, 0.003517] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 0.7925 [0.7796, 0.8054] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 7.288 [7.14, 7.436] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 7.9 [7.751, 8.048] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 3.655 [3.509, 3.802] MiB |
| PSS steady state (`daemon_pss_mib`) | 1.517 [1.37, 1.663] MiB |
| USS steady state (`daemon_uss_mib`) | 1.453 [1.307, 1.6] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 4.792 [3.542, 6.042] MiB |
| Threads (`daemon_threads`) | 1 [1, 1] count |
| Open FDs (`daemon_fds`) | 1262 [1262, 1262] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1252 [1252, 1252] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 1.409 [1.387, 1.431] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.1756 [0.03882, 0.3123] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 51.9 [51.9, 51.9] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 3.209e+06 [3.203e+06, 3.214e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 944.5 [944.5, 944.5] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 2388 [2385, 2391] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 2.674e+05 [2.669e+05, 2.678e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 30.55 [30.21, 30.89] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.005394 [0.002982, 0.007806] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 3.32 [3.076, 3.564] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 0.8065 [0.6357, 0.9773] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 1.574 [1.561, 1.586] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 7.977e+04 [7.974e+04, 7.979e+04] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 8537 [7893, 9181] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 79.83 [41.46, 118.2] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -73.6 [-274.3, 127.1] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | 9.559 [9.249, 9.869] W |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 55.89 [22.02, 89.77] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.4193 [0.3932, 0.4453] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.005 [1.005, 1.005] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.008 [1.007, 1.008] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 4.181e+04 [-2.218e+04, 1.058e+05] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 1 [1, 1] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.006204 [-0.006884, 0.01929] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.4374 [0.3854, 0.4894] | 0.4873 | — | — | 0.5326 | -17.22 | 0.3595 | 0 | 0 |  |
| x86_perf | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx | 0.3444 [0.2965, 0.3922] | 0.3837 | — | — | 1.058 | -92.55 | -6.637e-06 | 1252 | 0 |  |
| ib_mad | host_opa | 0.02697 [0.02013, 0.03381] | 0.03006 | — | — | 1.759 | -109.8 | 0.002399 | 0 | 0 |  |

Framework (NONE): 0.07805 core-s/h
Interaction residual: 0.01117 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 62

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 62 | 15.64 | 15.78 | 0.1338 |
| lustre_osc | 62 | 2.018 | 2.04 | 0.02132 |
| host_proc | 62 | 1.871 | 1.889 | 0.01781 |
| host_cpu | 62 | 1.536 | 1.549 | 0.01281 |
| lustre_llite | 62 | 1.235 | 1.715 | 0.4805 |
| host_net | 62 | 1.007 | 1.016 | 0.009417 |
| host_nfs | 62 | 0.7903 | 0.8009 | 0.01056 |
| intel_x86_uncore_imc_icx | 62 | 0.515 | 0.5232 | 0.008127 |
| host_mem | 62 | 0.1839 | 0.1886 | 0.0047 |
| host_opa | 62 | 0.1685 | 0.1735 | 0.004989 |
| host_ps | 62 | 0.158 | 0.1626 | 0.004573 |
| host_numa | 62 | 0.1436 | 0.1447 | 0.001029 |
| lustre_mdc | 62 | 0.1433 | 0.1468 | 0.003566 |
| host_vm | 62 | 0.1237 | 0.1242 | 0.0005406 |
| host_roofline_peak | 62 | 0.1199 | 0.1839 | 0.06407 |
| beegfs_client | 62 | 0.09961 | 0.1 | 0.0004266 |
| host_tmpfs | 62 | 0.08531 | 0.08546 | 0.0001784 |
| intel_x86_rapl | 62 | 0.04452 | 0.04451 | 6.129e-07 |
| host_block | 62 | 0.02922 | 0.02922 | 7.387e-06 |
| host_sysv_shm | 62 | 0.02611 | 0.0266 | 0.000501 |
| host_vfs | 62 | 0.02144 | 0.02146 | 1.39e-05 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group x86_perf on 80 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 1.5590 | 79,735 | 996.7 | 19.55 |
| ALL - NONE | 6 | 1.5597 | 79,738 | 996.7 | 19.56 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 385755 | hpcperfstatsd | 0.9122 | 0.1601 | 0.7404 | 1.43 | 0.6832 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 1.59 | 799.3/1898/3004 | 1.43 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 1.609 | 797.5/1898/3004 | 1.42 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.0231 | 798.5/3400/3400 | 0.96 |  |
| performance-b1-OFF- | performance | ok | False | 0.02946 | 797.8/3400/3400 | — |  |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.0234 | 797.9/3400/3400 | 1.366 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.02379 | 798.8/3400/3400 | 0.9644 | host_opa |
| performance-b1-SOLO-x86_perf | performance | ok | False | 1.607 | 793.2/1898/3004 | 0.96 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx |
| performance-b2-ALL-first | performance | ok | False | 1.605 | 799.3/1898/3005 | 1.426 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 1.606 | 799.3/1898/3004 | 1.386 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.02416 | 797.8/3400/3400 | 0.9633 |  |
| performance-b2-OFF- | performance | ok | False | 0.02954 | 778.3/3400/3400 | — |  |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.02505 | 797.7/3400/3400 | 1.37 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.02497 | 798/3400/3400 | 0.9644 | host_opa |
| performance-b2-SOLO-x86_perf | performance | ok | False | 1.615 | 799.3/1898/3004 | 0.9628 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx |
| performance-b3-ALL-first | performance | ok | False | 1.615 | 799.3/1898/3005 | 1.387 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 1.598 | 799.3/1898/3005 | 1.403 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 1.601 | 799.3/1898/3005 | 1.131 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.02388 | 797.7/3400/3400 | 0.9633 |  |
| performance-b3-OFF- | performance | ok | False | 0.02996 | 798.8/3400/3400 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.03106 | 798.8/3400/3400 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 1.585 | 799.3/1898/3005 | 1.441 | beegfs_client, host_block, host_cpu, host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_opa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.02354 | 794.7/3400/3400 | 1.229 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.02474 | 798.4/3400/3400 | 0.965 | host_opa |
| performance-b3-SOLO-x86_perf | performance | ok | False | 1.588 | 799.3/1898/3005 | 0.9639 | host_cpu_hw, intel_x86_rapl, intel_x86_uncore_imc_icx |
