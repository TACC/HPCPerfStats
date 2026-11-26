# hpcperfstatsd overhead profile — c122-018 (aarch64, Neoverse-V2)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 19.31 [19.3, 19.32] /s | 2.402 [2.209, 2.596] core-s/h | 3348 [3348, 3348] MHz | 0.04316 [0.0425, 0.04383] cores |

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 2.402 [2.209, 2.596] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 57.66 [53.01, 62.3] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.06673 [0.06519, 0.06827] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0004634 [0.0004527, 0.0004741] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.1301 [0.04002, 0.2201] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 2.275 [2.165, 2.385] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0 [0, 0] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.002957 [0.001721, 0.004192] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 2.383 [2.263, 2.503] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 19.58 [18.93, 20.22] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 21.23 [19.99, 22.47] CPU-ms |
| CPU per send (`cost_ms_per_send`) | NOT_MEASURED (ms_per_send not available in ALL legs) |
| RSS steady state (`daemon_rss_mib`) | 30.16 [30.11, 30.21] MiB |
| PSS steady state (`daemon_pss_mib`) | 25.77 [25.72, 25.82] MiB |
| USS steady state (`daemon_uss_mib`) | 24.04 [23.98, 24.09] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 29.92 [29.89, 29.95] MiB |
| Threads (`daemon_threads`) | 2 [2, 2] count |
| Open FDs (`daemon_fds`) | 1481 [1481, 1481] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 1300 [1300, 1300] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 19.31 [19.3, 19.32] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.0705 [0.06995, 0.07106] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 72.09 [72.09, 72.1] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 5.075e+06 [5.053e+06, 5.097e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.5 [960.5, 960.5] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 3679 [3665, 3693] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 4.229e+05 [4.211e+05, 4.247e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 0.5144 [0.4836, 0.5453] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.3176 [0.3078, 0.3275] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 29.92 [29.89, 29.95] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 2.46 [1.922, 2.998] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.01877 [0.01434, 0.02319] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 73.59 [-39.57, 186.8] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 21.15 [-105.1, 147.4] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 70.58 [56.04, 85.12] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | -203.6 [-842.4, 435.1] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | NOT_MEASURED (no RAPL powercap or hwmon energy counters readable) |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 87.04 [49.51, 124.6] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | -0.005493 [-0.09406, 0.08307] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 0.9998 [0.9983, 1.001] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1 [0.9984, 1.002] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 5891 [1362, 1.042e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.01068 [0.00501, 0.01635] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.01671 [-0.02855, 0.06198] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_mem_job, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.682 [0.5576, 0.8064] | 0.284 | 5.592 | 6.389 | 0.2631 | 1.377e+06 | 0.0007659 | 0 | 0.0009333 |  |
| ib_mad | host_ib | 0.5509 [0.4414, 0.6604] | 0.2292 | 4.534 | 4.807 | 0.625 | 4.338e+04 | 19.18 | 0 | 0.001001 |  |
| arm_cpu | host_cpu_hw | 0.3685 [0.2785, 0.4585] | 0.1534 | 2.818 | 3.506 | 0.6401 | 3.546e+06 | -0.009981 | 1296 | 0.0004689 |  |
| dcgm_gpu | nvidia_gpu | 0.242 [0.1648, 0.3193] | 0.1008 | 1.946 | 1.954 | 21.13 | 5.713e+04 | 0.0008765 | 0 | 0.2854 |  |
| hwmon_power | host_power_socket | 0.1671 [0.09679, 0.2375] | 0.06957 | 1.401 | 1.39 | -0.02083 | 8873 | -0.0113 | 0 | 0.0002869 |  |
| perf_event | arm_aarch64_imc | 0.009469 [-0.04597, 0.06491] | 0.003972 | 0.0724 | 0.07346 | -0.04167 | 1.735e+04 | 0.002038 | 4 | 0.0001385 |  |

Framework (NONE): 0.3439 core-s/h
Interaction residual: 0.03853 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 28.2 | 28.2 | 0.00134 |
| host_ib | 63 | 5.023 | 28.55 | 23.53 |
| nvidia_gpu | 63 | 3.827 | 3.827 | 7.714e-06 |
| host_numa | 63 | 3.809 | 3.809 | 1.889e-06 |
| host_mem | 63 | 3.495 | 3.495 | 3.048e-06 |
| host_cpu | 63 | 3.295 | 3.295 | 6.508e-07 |
| host_proc | 63 | 1.788 | 1.788 | 0 |
| host_power_socket | 63 | 1.428 | 1.428 | 6.937e-06 |
| host_net | 63 | 1.333 | 1.333 | 8.095e-07 |
| host_nfs | 63 | 0.6533 | 0.6532 | 1.27e-07 |
| host_ps | 63 | 0.2813 | 0.2813 | 1.079e-05 |
| host_vm | 63 | 0.2351 | 0.2351 | 6.222e-06 |
| host_roofline_peak | 63 | 0.1446 | 5.563 | 5.418 |
| host_block | 63 | 0.1375 | 0.1375 | 9.238e-06 |
| beegfs_client | 63 | 0.1077 | 0.1075 | 0 |
| host_tmpfs | 63 | 0.09889 | 0.09867 | 0 |
| host_sysv_shm | 63 | 0.03278 | 0.03275 | 5.238e-07 |
| host_vfs | 63 | 0.02993 | 0.02992 | 7.111e-06 |
| arm_aarch64_imc | 63 | 0.0288 | 0.03037 | 0.001574 |
| lustre_llite | 63 | 0.008189 | 0.008164 | 2.048e-06 |
| host_mem_job | 63 | 0.001075 | 0.001076 | 7.111e-06 |
| lustre_mdc | 63 | 0.0005714 | 0.0005719 | 5.587e-06 |
| lustre_osc | 63 | 0.0005404 | 0.000544 | 9.143e-06 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 1998876 | hpcperfstatsd | 2.384 | 0.2201 | 2.161 | 19.31 | 0.1334 |
| 1998881 | cuda0016cc005b4 | 0 | 0 | 0 | 0 | 0 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.06142 | 3285/3348/3375 | 19.31 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.06374 | 3285/3348/3375 | 19.32 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.0531 | 3285/3348/3375 | 0.1334 |  |
| performance-b1-OFF- | performance | ok | False | 0.04304 | 3285/3348/3375 | — |  |
| performance-b1-SOLO-arm_cpu | performance | ok | False | 0.06345 | 3285/3348/3375 | 0.1345 | host_cpu_hw |
| performance-b1-SOLO-dcgm_gpu | performance | ok | False | 0.04413 | 3285/3348/3375 | 0.135 | nvidia_gpu |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.04929 | 3285/3348/3375 | 0.1345 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-hwmon_power | performance | ok | False | 0.05139 | 3285/3348/3375 | 0.1333 | host_power_socket |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.05012 | 3285/3348/3375 | 19.31 | host_ib |
| performance-b1-SOLO-perf_event | performance | ok | False | 0.04325 | 3285/3348/3375 | 0.1395 | arm_aarch64_imc |
| performance-b2-ALL-first | performance | ok | False | 0.06354 | 3285/3348/3375 | 19.3 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.06417 | 3285/3348/3375 | 19.32 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.04511 | 3285/3348/3375 | 0.1333 |  |
| performance-b2-OFF- | performance | ok | False | 0.04269 | 3285/3348/3375 | — |  |
| performance-b2-SOLO-arm_cpu | performance | ok | False | 0.06343 | 3285/3348/3375 | 0.1345 | host_cpu_hw |
| performance-b2-SOLO-dcgm_gpu | performance | ok | False | 0.04993 | 3285/3348/3375 | 0.1339 | nvidia_gpu |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.04361 | 3285/3348/3375 | 0.134 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-hwmon_power | performance | ok | False | 0.04424 | 3285/3348/3375 | 0.1001 | host_power_socket |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.0472 | 3285/3348/3375 | 19.32 | host_ib |
| performance-b2-SOLO-perf_event | performance | ok | False | 0.04588 | 3285/3348/3375 | 0.1333 | arm_aarch64_imc |
| performance-b3-ALL-first | performance | ok | False | 0.05542 | 3285/3348/3375 | 19.31 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.06327 | 3285/3348/3375 | 19.32 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.06313 | 3285/3348/3375 | 9.762 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.05327 | 3285/3348/3375 | 0.1334 |  |
| performance-b3-OFF- | performance | ok | False | 0.04291 | 3285/3348/3375 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.04401 | 3285/3348/3375 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.06477 | 3285/3348/3375 | 19.32 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-arm_cpu | performance | ok | False | 0.06316 | 3285/3348/3375 | 0.1012 | host_cpu_hw |
| performance-b3-SOLO-dcgm_gpu | performance | ok | False | 0.04973 | 3285/3348/3375 | 0.1339 | nvidia_gpu |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.05283 | 3285/3348/3375 | 0.134 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_mem_job, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-hwmon_power | performance | ok | False | 0.04707 | 3285/3348/3375 | 0.1329 | host_power_socket |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.04934 | 3285/3348/3375 | 19.31 | host_ib |
| performance-b3-SOLO-perf_event | performance | ok | False | 0.04272 | 3285/3348/3375 | 0.1334 | arm_aarch64_imc |
