# hpcperfstatsd overhead profile — c642-041 (aarch64, Neoverse-V2)

## Headline: daemon wakeups/s and CPU per governor

Wakeups/s (voluntary + nonvoluntary context switches of every daemon thread) does not depend on the governor; core-s/h does (finding 18), so it is only comparable under the same governor.

| Governor | Daemon wakeups/s (ALL) | Daemon CPU core-s/h (ALL) | Clock p50 MHz (ALL) | Node busy cores (OFF) |
| --- | --- | --- | --- | --- |
| performance | 6.775 [6.616, 6.934] /s | 0.9051 [0.8363, 0.9739] core-s/h | 3348 [3348, 3348] MHz | 0.02576 [0.02291, 0.02861] cores |

> baseline_drift — governor `performance` for NONE: comparisons involving them are not citable.

## Headline metrics — governor `performance`

| Metric | Value |
| --- | --- |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_h`) | 0.9051 [0.8363, 0.9739] core-s/h |
| Daemon CPU (ALL) (`daemon_cpu_core_s_per_day`) | 21.72 [20.07, 23.37] core-s/day |
| Daemon CPU, % of one core (`daemon_cpu_pct_one_core`) | 0.02514 [0.02348, 0.0268] % |
| Daemon CPU, % of node capacity (`daemon_cpu_pct_node`) | 0.0003492 [0.0003261, 0.0003723] % |
| Daemon user CPU (`daemon_user_s_per_h`) | 0.09335 [0.04331, 0.1434] s/h |
| Daemon system CPU (`daemon_system_s_per_h`) | 0.8068 [0.697, 0.9166] s/h |
| 1 s CPU p50 (`daemon_cpu_1s_p50_pct`) | 0.001373 [0.0001083, 0.002638] % of one core |
| 1 s CPU p95 (`daemon_cpu_1s_p95_pct`) | 0.003553 [0.002534, 0.004573] % of one core |
| 1 s CPU max (`daemon_cpu_1s_max_pct`) | 0.8214 [0.8039, 0.839] % of one core |
| CPU per fast-tier sample (`cost_ms_per_fast_sample`) | 6.972 [6.838, 7.105] CPU-ms |
| CPU per full-tier sample (`cost_ms_per_full_sample`) | 7.584 [7.416, 7.752] CPU-ms |
| CPU per send (`cost_ms_per_send`) | 3.974 CPU-ms |
| RSS steady state (`daemon_rss_mib`) | 30.29 [29.18, 31.39] MiB |
| PSS steady state (`daemon_pss_mib`) | 25.91 [25.75, 26.07] MiB |
| USS steady state (`daemon_uss_mib`) | 23.72 [23.71, 23.72] MiB |
| VmHWM (`daemon_vmhwm_mib`) | 30.5 [29.19, 31.81] MiB |
| Threads (`daemon_threads`) | 2 [2, 2] count |
| Open FDs (`daemon_fds`) | 749 [749, 749] count |
| perf_event FDs held (`daemon_perf_event_fds`) | 650 [650, 650] count |
| Context switches (vol+invol) (`daemon_wakeups_per_s`) | 6.775 [6.616, 6.934] /s |
| Minor faults (`daemon_minflt_per_s`) | 0.02436 [0.01269, 0.03602] /s |
| Major faults (`daemon_majflt_per_s`) | 0 [0, 0] /s |
| Block read (`daemon_read_bytes_per_h`) | 0 [0, 0] bytes/h |
| Block write (`daemon_write_bytes_per_h`) | 0 [0, 0] bytes/h |
| Read+write syscalls (`daemon_syscalls_per_s`) | 35.13 [34.54, 35.71] /s |
| Broker bytes sent (acked) (`broker_bytes_acked_per_h`) | 2.576e+06 [2.536e+06, 2.616e+06] bytes/h |
| Broker bytes received (`broker_bytes_received_per_h`) | 960.2 [959.9, 960.4] bytes/h |
| Broker segments out (`broker_segs_out_per_h`) | 1954 [1928, 1980] segments/h |
| Broker bytes per send (`broker_bytes_per_send`) | 2.147e+05 [2.113e+05, 2.18e+05] bytes |
| Daemon IPC (`daemon_ipc`) | NOT_MEASURED (perf stat not run or events not counted) |
| Daemon cache-miss ratio (`daemon_cache_miss_ratio`) | NOT_MEASURED (perf stat not run or events not counted) |
| Startup wall time to Enabled types (`startup_wall_s`) | 0.1614 [0.1208, 0.2019] s |
| Startup CPU above steady state (`startup_cpu_s`) | 0.01979 [0.01742, 0.02216] CPU-s |
| Startup peak RSS (`startup_peak_rss_mib`) | 29.62 [29.62, 29.62] MiB |
| Daemon CPU under full FWQ load (`daemon_cpu_core_s_per_h_under_load`) | 0.9215 [0.7387, 1.104] core-s/h |
| Node busy CPU, ALL − OFF (`node_busy_cores_delta`) | 0.0009727 [-0.003048, 0.004993] cores |
| Interrupts, ALL − OFF (`node_irq_per_s_delta`) | 7041 [7023, 7059] /s |
| Softirqs, ALL − OFF (`node_softirq_per_s_delta`) | 32.68 [9.401, 55.96] /s |
| Node context switches, ALL − OFF (`node_ctxt_per_s_delta`) | 78.01 [31.49, 124.5] /s |
| PSI cpu some avg10, ALL − OFF (`node_psi_cpu_some_avg10_delta`) | NOT_MEASURED (PSI not available (kernel without CONFIG_PSI or psi=0)) |
| Node memory used, ALL − OFF (`node_mem_used_mib_delta`) | 32.06 [-466.7, 530.8] MiB |
| Package+DRAM power, ALL − OFF (`node_power_w_delta`) | NOT_MEASURED (no RAPL powercap or hwmon energy counters readable) |
| Harness self-cost (sampler) (`harness_cpu_s_per_h`) | 36.68 [11.68, 61.68] core-s/h |
| FWQ mean slowdown, ALL vs OFF (`fwq_mean_slowdown_pct`) | 0.1082 [-0.1677, 0.3841] % |
| FWQ p99 quantum, ALL / OFF (`fwq_p99_ratio`) | 1.002 [0.9993, 1.005] ratio |
| FWQ p99.9 quantum, ALL / OFF (`fwq_p999_ratio`) | 1.003 [0.9997, 1.006] ratio |
| FWQ max detour (ALL) (`fwq_max_detour_us`) | 7488 [-1758, 1.673e+04] µs |
| FWQ quanta above OFF p99 (ALL) (`fwq_frac_above_off_p99`) | 0.02741 [0.009031, 0.04579] fraction |
| FWQ mean slowdown, NONE vs OFF (`fwq_none_slowdown_pct`) | 0.05149 [-0.2118, 0.3148] % |
| GPU-side interference (`gpu_interference`) | NOT_MEASURED (out of scope (CPU-only study)) |

## Per-driver cost (SOLO − NONE, paired within block) — governor `performance`

| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| filesystem | beegfs_client, host_block, host_cpu, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm, lustre_llite, lustre_mdc, lustre_osc | 0.4465 [0.3961, 0.4969] | 0.4934 | — | — | 0.263 | 7.285e+05 | 0.9533 | 0 | 0.001456 |  |
| arm_cpu | host_cpu_hw | 0.1977 [0.1402, 0.2551] | 0.2179 | — | — | 0.2076 | 1.806e+06 | 0.0007472 | 648 | -4.253e-06 |  |
| ib_mad | host_ib | 0.09394 [0.0751, 0.1128] | 0.1038 | — | — | 0.1875 | 1.298e+04 | 4.75 | 0 | -3.493e-06 |  |
| hwmon_power | host_power_socket | 0.07735 [0.06676, 0.08794] | 0.08532 | — | — | 0 | 4565 | 0.06537 | 0 | 5.679e-05 |  |
| dcgm_gpu | nvidia_gpu | 0.06605 [0.03032, 0.1018] | 0.07209 | — | — | 21.48 | 1.381e+04 | 6.193e-06 | 0 | 0.007703 |  |
| perf_event | arm_aarch64_imc | 0.01703 [-0.0249, 0.05896] | 0.01716 | — | — | 0 | 8667 | 6.182e-06 | 2 | -4.253e-06 |  |

Framework (NONE): 0.04844 core-s/h
Interaction residual: -0.04181 core-s/h

Groups include each type's incidental /proc reads (decision 11). Bursts that coincide with a sample are counted as that sample, so CPU per send covers only unaligned sends.

## Instrumented collector profile

> instrumented build (--enable-metric-profiler), not production; ranks collector types for diagnosis only and never feeds a headline number

Cycles: 63

| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |
| --- | --- | --- | --- | --- |
| host_cpu_hw | 63 | 7.985 | 7.985 | 0 |
| host_proc | 63 | 2.048 | 2.062 | 0.0143 |
| lustre_osc | 63 | 1.559 | 1.559 | 0 |
| host_cpu | 63 | 1.041 | 1.041 | 0 |
| nvidia_gpu | 63 | 0.9476 | 0.9478 | 0.0001877 |
| lustre_llite | 63 | 0.8872 | 15.35 | 14.47 |
| host_ib | 63 | 0.867 | 5.745 | 4.878 |
| host_power_socket | 63 | 0.6076 | 0.6687 | 0.0611 |
| host_mem | 63 | 0.5669 | 0.5669 | 1.302e-06 |
| host_nfs | 63 | 0.5426 | 0.5425 | 4.286e-07 |
| host_net | 63 | 0.5104 | 0.5104 | 0 |
| host_numa | 63 | 0.3727 | 0.3726 | 0 |
| host_ps | 63 | 0.1285 | 0.1285 | 1.476e-06 |
| lustre_mdc | 63 | 0.1127 | 0.1126 | 0 |
| host_vm | 63 | 0.1072 | 0.1072 | 6.413e-06 |
| beegfs_client | 63 | 0.09632 | 0.09623 | 0 |
| host_block | 63 | 0.09417 | 0.09414 | 4.921e-07 |
| host_roofline_peak | 63 | 0.08479 | 0.6088 | 0.524 |
| host_tmpfs | 63 | 0.06971 | 0.06965 | 0 |
| host_sysv_shm | 63 | 0.02198 | 0.02196 | 0 |
| host_vfs | 63 | 0.02046 | 0.02046 | 9.079e-06 |
| arm_aarch64_imc | 63 | 0.01208 | 0.01274 | 0.0006617 |

## CPU counter rotation (kernel multiplexing, node interrupt cost)

Group arm_cpu on 72 CPUs, minus the same block's NONE leg. Charged to interrupt time on every core, not to the daemon.

| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |
| --- | --- | --- | --- | --- | --- |
| SOLO - NONE | 3 | 0.0000 | 6,990 | 97.1 | 0.00 |
| ALL - NONE | 6 | 0.0000 | 7,012 | 97.4 | 0.00 |

## Daemon threads (hottest, first usable ALL leg) — governor `performance`

Leg performance-b1-ALL-first. Embedded DCGM/NVML workers inherit the daemon's name; match a tid to its library with `gdb -p PID -batch -ex 'thread apply all bt'`. Active = share of 1 s windows with run time > 0 (steady loop ~1, burst small); — = not in this harness version's raw samples.

| tid | comm | core-s/h | user s/h | system s/h | wakeups/s | active |
| --- | --- | --- | --- | --- | --- | --- |
| 455196 | hpcperfstatsd | 0.8742 | 0.1 | 0.76 | 6.848 | 1 |
| 455201 | cuda000b7c002e0 | 0 | 0 | 0 | 0 | 0 |

## Legs

| Leg | Governor | Status | Contaminated | Node busy cores | Clock MHz min/p50/max | Wakeups/s | Verified types |
| --- | --- | --- | --- | --- | --- | --- | --- |
| performance-b1-ALL-first | performance | ok | False | 0.02463 | 3330/3348/3375 | 6.848 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-ALL-second | performance | ok | False | 0.03027 | 3330/3348/3375 | 6.536 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-NONE- | performance | ok | False | 0.03043 | 3330/3348/3375 | 1.033 |  |
| performance-b1-OFF- | performance | ok | False | 0.02426 | 3330/3348/3375 | — |  |
| performance-b1-SOLO-arm_cpu | performance | ok | False | 0.02477 | 3330/3348/3375 | 1.034 | host_cpu_hw |
| performance-b1-SOLO-dcgm_gpu | performance | ok | False | 0.02447 | 3330/3348/3375 | 1.033 | nvidia_gpu |
| performance-b1-SOLO-filesystem | performance | ok | False | 0.02527 | 3330/3348/3375 | 2.001 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b1-SOLO-hwmon_power | performance | ok | False | 0.02444 | 3330/3348/3375 | 1.163 | host_power_socket |
| performance-b1-SOLO-ib_mad | performance | ok | False | 0.02987 | 3330/3348/3375 | 5.76 | host_ib |
| performance-b1-SOLO-perf_event | performance | ok | False | 0.02458 | 3330/3348/3375 | 1.033 | arm_aarch64_imc |
| performance-b2-ALL-first | performance | ok | False | 0.0303 | 3330/3348/3375 | 6.853 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-ALL-second | performance | ok | False | 0.02463 | 3330/3348/3375 | 6.838 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-NONE- | performance | ok | False | 0.02446 | 3330/3348/3375 | 1.033 |  |
| performance-b2-OFF- | performance | ok | False | 0.02981 | 3330/3348/3375 | — |  |
| performance-b2-SOLO-arm_cpu | performance | ok | False | 0.03016 | 3330/3348/3375 | 1.034 | host_cpu_hw |
| performance-b2-SOLO-dcgm_gpu | performance | ok | False | 0.02508 | 3330/3348/3375 | 1.033 | nvidia_gpu |
| performance-b2-SOLO-filesystem | performance | ok | False | 0.02458 | 3330/3348/3375 | 2.017 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b2-SOLO-hwmon_power | performance | ok | False | 0.02455 | 3330/3348/3375 | 1.1 | host_power_socket |
| performance-b2-SOLO-ib_mad | performance | ok | False | 0.0249 | 3330/3348/3375 | 5.796 | host_ib |
| performance-b2-SOLO-perf_event | performance | ok | False | 0.0301 | 3330/3348/3375 | 1.033 | arm_aarch64_imc |
| performance-b3-ALL-first | performance | ok | False | 0.0249 | 3330/3348/3375 | 6.843 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-ALL-second | performance | ok | False | 0.02568 | 3330/3348/3375 | 6.732 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-CADENCE-60.0 | performance | ok | False | 0.02467 | 3330/3348/3375 | 4.028 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-NONE- | performance | ok | False | 0.03032 | 3330/3348/3375 | 1.033 |  |
| performance-b3-OFF- | performance | ok | False | 0.02445 | 3330/3348/3375 | — |  |
| performance-b3-OFF-end | performance | ok | False | 0.02453 | 3330/3348/3375 | — |  |
| performance-b3-PROFILE- | performance | ok | False | 0.02538 | 3330/3348/3375 | 6.849 | arm_aarch64_imc, host_power_socket, beegfs_client, host_block, host_cpu, host_cpu_hw, host_ib, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, nvidia_gpu, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-arm_cpu | performance | ok | False | 0.02434 | 3330/3348/3375 | 1.034 | host_cpu_hw |
| performance-b3-SOLO-dcgm_gpu | performance | ok | False | 0.02441 | 3330/3348/3375 | 1.033 | nvidia_gpu |
| performance-b3-SOLO-filesystem | performance | ok | False | 0.02462 | 3330/3348/3375 | 1.942 | beegfs_client, host_block, host_cpu, lustre_llite, lustre_mdc, lustre_osc, host_mem, host_net, host_nfs, host_numa, host_proc, host_ps, host_roofline_peak, host_sysv_shm, host_tmpfs, host_vfs, host_vm |
| performance-b3-SOLO-hwmon_power | performance | ok | False | 0.02448 | 3330/3348/3375 | 1.033 | host_power_socket |
| performance-b3-SOLO-ib_mad | performance | ok | False | 0.02452 | 3330/3348/3375 | 5.793 | host_ib |
| performance-b3-SOLO-perf_event | performance | ok | False | 0.02429 | 3330/3348/3375 | 1.033 | arm_aarch64_imc |
