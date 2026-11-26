# Run comparison (per-leg parity)

Verdict: **COMPARABLE** -- every must-match row is identical and no run shortened or dropped legs.

## Must match (per-leg settings and daemon conf; broker connection excluded)

| Item | Status | c122-018 | c313-031 | c317-016 | c318-005 | c460-220 | c492-081 | c512-122 | c551-001 | c561-009 | c573-002 | c642-041 | i618-141 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| build.version | same | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 | 3.1 |
| schedule.leg_seconds | same | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 | 1800 |
| schedule.settle_seconds | same | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 60 | 60 |
| schedule.fwq_seconds | same | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 |
| schedule.blocks | same | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| schedule.governors | same | ["performance"] | none (no cpufreq) | ["performance"] | ["performance"] | ["performance"] | ["performance"] | ["performance"] | ["performance"] | ["performance"] | ["performance"] | ["performance"] | ["performance"] |
| schedule.off_legs | same | true | true | true | true | true | true | true | true | true | true | true | true |
| conf.buffer_hours | same | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 | 6 |
| conf.emit_value_sentinels | same | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| conf.enable_slow_tier | same | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| conf.port | same | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 | 5672 |
| conf.sample_freq | same | 30 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | 30 | 30 |
| conf.sample_freq_slow | same | 600 | 600 | 600 | 600 | 600 | 600 | 600 | 600 | 600 | 600 | 600 | 600 |
| conf.send_freq | same | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 | 300 |
| fit_steps | same | [] | [] | [] | [] | [] | [] | [] | [] | [] | [] | [] | [] |

## Intended differences (fleet build, OS, hardware; recorded, not a failure)

| Item | c122-018 | c313-031 | c317-016 | c318-005 | c460-220 | c492-081 | c512-122 | c551-001 | c561-009 | c573-002 | c642-041 | i618-141 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| build.rpm_nvra | hpcperfstatsd-3.1-1.el10 | hpcperfstatsd-3.1-1.el8 | hpcperfstatsd-3.1-1.el8 | hpcperfstatsd-3.1-1.el8 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 | hpcperfstatsd-3.1-1.el9 |
| build.capability_slug | aarch64-ver3.1-hw-ib-ibdyn-nvgpu-lustre-beegfs-arm-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-lustre-beegfs-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-lustre-beegfs-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-lustre-beegfs-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-intelgpu-lustre-beegfs-opa-opadyn-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-intelgpu-lustre-beegfs-opa-opadyn-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-intelgpu-lustre-beegfs-opa-opadyn-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-intelgpu-lustre-beegfs-opa-opadyn-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-intelgpu-lustre-beegfs-opa-opadyn-x86perf-sent1-slowtier1 | x86_64-ver3.1-hw-ib-ibdyn-nvgpu-intelgpu-lustre-beegfs-opa-opadyn-x86perf-sent1-slowtier1 | aarch64-ver3.1-hw-ib-nvgpu-lustre-beegfs-arm-sent1-slowtier1 | aarch64-ver3.1-hw-ib-nvgpu-lustre-beegfs-arm-sent1-slowtier1 |
| build.fleet | horizon | ls6 | ls6 | ls6 | stampede3 | stampede3 | stampede3 | stampede3 | stampede3 | stampede3 | — | — |
| build.shared_node_multi_jid | true | false | false | false | false | false | false | false | false | false | false | false |
| build.ib_mad_dlopen | true | true | true | true | true | true | true | true | true | true | false | false |
| build.git_commit | 2e17c0523efb | 875c38421849 | 23967bf83136 | 875c38421849 | 23967bf83136 | 23967bf83136 | 2088a83732c7 | 7b016d9dc1e6 | 2088a83732c7 | 4ed5a9a4b4f1 | 1eb9497b636d | 1eb9497b636d |
| build.git_dirty | false | false | false | false | false | false | true | false | false | false | false | false |
| host.kernel | 6.12.0-211.61.1.el10_2.aarch64+64k | 4.18.0-553.150.1.el8_10.x86_64 | 4.18.0-553.150.1.el8_10.x86_64 | 4.18.0-553.150.1.el8_10.x86_64 | 5.14.0-687.66.1.el9_8.x86_64 | 5.14.0-687.66.1.el9_8.x86_64 | 5.14.0-687.66.1.el9_8.x86_64 | 5.14.0-611.24.1.el9_7.x86_64 | 5.14.0-687.66.1.el9_8.x86_64 | 5.14.0-687.66.1.el9_8.x86_64 | 5.14.0-687.65.1.el9_8.aarch64+64k | 5.14.0-687.65.1.el9_8.aarch64+64k |
| host.os | Rocky Linux 10.2 (Red Quartz) | Rocky Linux 8.10 (Green Obsidian) | Rocky Linux 8.10 (Green Obsidian) | Rocky Linux 8.10 (Green Obsidian) | Rocky Linux 9.8 (Blue Onyx) | Rocky Linux 9.8 (Blue Onyx) | Rocky Linux 9.8 (Blue Onyx) | Rocky Linux 9.7 (Blue Onyx) | Rocky Linux 9.8 (Blue Onyx) | Rocky Linux 9.8 (Blue Onyx) | Rocky Linux 9.8 (Blue Onyx) | Rocky Linux 9.8 (Blue Onyx) |
| host.nproc_online | 144 | 128 | 128 | 96 | 80 | 48 | 112 | 96 | 96 | 128 | 72 | 144 |
| host.numa_nodes | 34 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 8 | 9 | 2 |
| host.mem_gib | 1693 | 251 | 251 | 377 | 251 | 187 | 125 | 1007 | 1007 | 1510 | 118 | 238 |
| host.gpus | 4 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 4 | 8 | 1 | 0 |
| host.pmu_sharers | nvidia-powerd=active | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive | nvidia-powerd=inactive |
| host.cpufreq | ondemand | — | performance | performance | performance | performance | performance | performance | performance | performance | performance | performance |
| host.other_perf_holders | — | — | — | — | — | — | — | vnfs-collector=148,xpumd=648 | — | — | — | — |
| host.nmi_watchdog | — | — | — | — | — | — | — | 1 | — | — | — | — |
| groups | arm_cpu,dcgm_gpu,filesystem,hwmon_power,ib_mad,perf_event | filesystem,ib_mad,x86_perf | dcgm_gpu,filesystem,ib_mad,x86_perf | filesystem,ib_mad,x86_perf | filesystem,ib_mad,x86_perf | filesystem,ib_mad,x86_perf | filesystem,ib_mad,x86_perf | filesystem,ib_mad,intel_gpu,x86_perf | dcgm_gpu,filesystem,ib_mad,x86_perf | dcgm_gpu,filesystem,ib_mad,x86_perf | arm_cpu,dcgm_gpu,filesystem,hwmon_power,ib_mad,perf_event | arm_cpu,filesystem,hwmon_power,ib_mad,perf_event |
| variants | - | - | - | - | - | - | - | - | - | - | - | - |
| legs | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx18 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx9 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx12 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx9 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx9 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx9 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx9 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx12 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx12 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx12 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx18 | ALLx6 CADENCEx1 NONEx3 OFFx4 PROFILEx1 SOLOx15 |
| flags | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left | measure_queue_left |

Per-leg data for every run: `raw/legs/<leg_id>/` (summary.json, samples.jsonl, journal.txt, perf.csv); leg status and contamination in `profile.json` -> `legs`.
