# Monitor overhead - campaign v31, all node types (2026-11-23 to 2026-11-26)

Recorded overhead profiles of `hpcperfstatsd` 3.1 (release RPM, real `hpcperfstats.service`) on one
reserved, idle node per node type, plus a counter-rotation check after each run.

## Summary

| Field | Value |
|--------|--------|
| Monitor | 3.1 release RPM, each cluster's own fleet build |
| Harness | `monitor/measurement/bin/run_overhead_profile.sh` via `monitor/measurement/bin/campaign_node_run.sh` |
| Conf | harness-built, identical except broker; `sample_freq 30`, `sample_freq_slow 600`, `send_freq 300` |
| Legs | 1800 s each, settle 60 s, FWQ 300 s, 3 blocks (OFF, NONE, ALL, SOLO per driver group, ALL, OFF) plus cadence and profile legs |
| Governor | `performance` (LS6 Milan c313-031: `none`, no cpufreq) |
| Runs | 12 node types, every leg ok, none contaminated |
| Comparability | `compare_runs.py` over all 12 runs: **COMPARABLE** ([`overhead_profile/campaign_v31_compare.md`](overhead_profile/campaign_v31_compare.md)) |
| Daemon CPU (ALL legs) | **0.82-2.76 core-s/h** on every node except LS6 Milan + A100 (724, `dcgm_gpu`) |
| CPU counter rotation (x86) | **0.39-1.56 cores** of interrupt time per node (1,400-5,600 core-s/h), 1,000-6,000x the daemon |
| Per-node profiles | [`overhead_profile/`](overhead_profile/) (one directory per node: `profile.json`, `report.md`, `profile_row.csv`, `counter_rotation_*.txt`) |
| Cross-node table | [`overhead_profile/profiles.md`](overhead_profile/profiles.md) (also `.csv`, `.tex`) |
| Raw data (outside git) | `<workspace_root>/paper_data/campaign_v31/<host>/` on hpcperfstats-dev: full run trees with `raw/`, run logs, check files; c573-002's node-produced derived files in `c573-002/node_original_derived/` |

### Per node

Rotation = ALL - NONE interrupt time (kernel perf multiplexing, charged to every core, not to the
daemon). Total = daemon + rotation.

| Node | Node type | CPUs | Kernel | Governor | Daemon core-s/h | Rotation cores | Rotation core-s/h | Total core-s/h | us per rotation |
|---|---|---|---|---|---|---|---|---|---|
| c573-002 | Stampede3 amd-rtx, AMD Turin 9555 | 128 | el9 5.14 | performance | 2.76 | 0.389 | 1,400 | 1,403 | 3.04 |
| c318-005 | LS6 Genoa 9454 | 96 | el8 4.18 | performance | 1.58 | 0.717 | 2,579 | 2,581 | 7.57 |
| c313-031 | LS6 Milan 7763 | 128 | el8 4.18 | none | 2.67 | 1.181 | 4,251 | 4,253 | 9.25 |
| c317-016 | LS6 Milan 7763 + A100 | 128 | el8 4.18 | performance | 724.1 | 1.483 | 5,339 | 6,063 | 11.69 |
| c492-081 | Stampede3 SKX 8160 | 48 | el9 5.14 | performance | 0.82 | 0.994 | 3,579 | 3,580 | 20.81 |
| c460-220 | Stampede3 ICX 8380 | 80 | el9 5.14 | performance | 0.90 | 1.560 | 5,615 | 5,616 | 19.56 |
| c512-122 | Stampede3 SPR Max 9480 | 112 | el9 5.14 | performance | 1.59 | 0.867 | 3,120 | 3,122 | 7.87 |
| c561-009 | Stampede3 SPR 8468 + H100 | 96 | el9 5.14 | performance | 1.57 | 0.559 | 2,014 | 2,015 | 5.85 |
| c551-001 | Stampede3 SPR 8468 + PVC | 96 | el9 5.14 | performance | 1.17 | 0.576 | 2,072 | 2,073 | 6.13 |
| c642-041 | Vista GH200 | 72 | el9 5.14 | performance | 0.91 | not measurable | - | >= 0.91 | - |
| i618-141 | Vista Grace-Grace | 144 | el9 5.14 | performance | 1.62 | not measurable | - | >= 1.62 | - |
| c122-018 | Horizon GB200 | 144 | el10 6.12 | ondemand | 2.40 | 0 added (nvidia-powerd already rotates) | 0 | 2.40 | - |

Grace kernels report 0 interrupt time, so rotation there (about 100 per second per CPU at a 10 ms
interval) cannot be measured with `/proc/stat`. Whole-node busy time ALL - OFF was 3.5 core-s/h (95%
CI -11 to 18) on GH200 and 38 (18 to 59) on Grace-Grace.

### Counterfactual (`counter_rotation_check.sh`, daemon stopped, 60 s each)

`fits` = a core event set that fits the general counters; `monitor` = the monitor's own set.

| Node | Other perf holders (fds) | NMI watchdog | none: irq cores | fits: intr/s per CPU, irq cores, event share | monitor: intr/s per CPU, irq cores, event share |
|---|---|---|---|---|---|
| c492-081 SKX | vnfs-collector 100 | 1 | 0.0010 | 1,001.5, 0.913, 81-90% | 1,001.3, 0.930, 53-66% |
| c460-220 ICX | vnfs-collector 132 | 1 | 0.0018 | 1,001.3, 1.562, 81-90% | 1,000.8, 1.583, 53-66% |
| c512-122 SPR Max | vnfs-collector 164 | 1 | 0.0035 | 1,008.7, 0.860, 81-90% | 1,008.9, 0.867, 53-66% |
| c561-009 SPR + H100 | vnfs-collector 148 | 1 | 0.0018 | 1,007.4, 0.547, 81-90% | 1,007.5, 0.552, 53-66% |
| c551-001 SPR + PVC | vnfs-collector 148, xpumd 648 | 1 | 0.0095 | 1,014.6, 0.559, 81-90% | 1,015.2, 0.565, 53-66% |
| c317-016 Milan + A100 | none | 1 | 0.0027 | 1,001.4, 1.489, 80% | 1,001.1, 1.516, 67-83% |
| c318-005 Genoa | none | 1 | 0.0020 | 1,001.5, 0.710, 80% | 1,001.3, 0.713, 67-83% |
| c318-005 Genoa, watchdog off | none | **0** | 0.0028 | **3.3, 0.0027, 100%** | 1,001.4, 0.759, 83% |
| c313-031 Milan | none | 1 | 0.0017 | 1,000.7, 1.160, 80% | 1,001.2, 1.180, 67-83% |
| c313-031 Milan, watchdog off | none | **0** | 0.0017 | **2.4, 0.0020, 100%** | 1,001.0, 1.244, 83% |
| c642-041 GH200 | none | 0 | 0.0000 | 12.4, 0.0000, 100% | 110.0, 0.0000, 66-77% |
| i618-141 Grace-Grace | none | 0 | 0.0000 | 4.6, 0.0002, 100% | 100.4, 0.0000, 66-77% |
| c122-018 GB200 | nvidia-powerd 3,168 | 0 | 0.0012 | 111.9, 0.0017, 24% | 111.3, 0.0003, 19-22% |

Other holders: `vnfs-collector` (VAST Data's eBPF NFS metrics daemon; kprobe attach points and BPF
output buffers) and `xpumd` (Intel XPU Manager; i915 GPU PMU events) hold **no** CPU core counter:
the fitting set's 90% share on every Intel node is the watchdog-only 8-of-9 share. `nvidia-powerd`
holds Grace core counters (undocumented by NVIDIA). No check exists for c573-002 (node no longer
available).

## What this supports

- **The daemon itself is cheap:** 0.82-2.76 core-s/h on every node type except LS6 Milan + A100,
  where in-daemon DCGM costs 724 core-s/h (H100 0.27, RTX 2.2).
- **On x86 the dominant cost is kernel CPU counter rotation**, caused by the monitor's own core event
  set exceeding the free general counters: 0.39-1.56 cores of interrupt time per node at about 1,000
  rotations per second per CPU.
- **Rotation is on/off and avoidable:** with the NMI watchdog off and no other holder, a 6-slot set on
  Genoa and Milan ran at idle interrupt rates; one slot more brought back the full cost.
- **The NMI watchdog (on in production on every checked x86 node) takes one counter**, so a set that
  fits on paper still rotates with it on.

## Limits (do not over-generalize)

- One node per node type, one run each; per-leg comparability only (total run time and collector sets
  differ by node type).
- Do not rank CPUs by us per rotation without kernel and governor alongside: the CPU is confounded with
  the kernel (el8 4.18, el9 5.14, el10 6.12) and with frequency control.
- Grace rotation cost is not measurable with `/proc/stat`; quote rotations per second and event share
  there, or the busy-time bounds above.
- Intel x86_perf legs show a lower harness clock reading (for example ICX 3400 -> 1898 MHz); most
  likely the idle-core frequency read changing once rotation interrupts wake the cores, not a
  slowdown. Not citable until checked.
- Counterfactual checks ran after the runs (ICX and LS6 one to two days later); holder counts are from
  the check time.
- LS6 H100 not measured (hardware fix pending).

## Suggested citation wording

Across 12 TACC node types (2026-11), `hpcperfstatsd` 3.1 used 0.8-2.8 core-seconds per hour of its own
CPU (724 on an A100 node, from in-process DCGM), while kernel multiplexing of its CPU counter set cost
0.39-1.56 cores of interrupt time per x86 node (1,400-5,600 core-seconds per hour); with a counter set
that fits the free counters, that cost disappears (profiles and checks in this directory).
