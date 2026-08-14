# Monitor CPU overhead — Stampede3 SPR (2026-08-14)

Recorded production measurement of `hpcperfstatsd` CPU use on a Stampede3 Sapphire Rapids (SPR) compute node.

## Summary

| Field | Value |
|--------|--------|
| Host | `c512-122.stampede3.tacc.utexas.edu` |
| Node class | Stampede3 SPR (`c512-122[spr]`) |
| Kernel | Linux 5.14.0-611.24.1.el9_7.x86_64 (112 CPUs) |
| Date | 2026-08-14 |
| Process | `hpcperfstatsd` PID `3695308` |
| Tool | `pidstat -p 3695308 10 60` (10 s interval, 60 samples ≈ 10 min) |
| Peak `%CPU` in any 10 s window | **3.00%** of one core |
| Other busy windows | 2.80%, 2.90%, 2.90% |
| Mean of busy windows | 2.90% of one core |
| `pidstat` Average over full window | **0.19%** of one core |
| Raw log | [`hpcperfstatsd_pidstat_c512-122_stampede3_spr_2026-08-14.txt`](hpcperfstatsd_pidstat_c512-122_stampede3_spr_2026-08-14.txt) |

## What this supports

- **During a sample window**, CPU use stayed at or below **3% of one core** on this SPR node (max observed 3.00%).
- **Over a ten-minute wall-clock window** that includes idle time between samples, average CPU use was **0.19% of one core**.
- Elevated `%CPU` appears in short periodic bursts (four busy 10 s bins in this capture, roughly 150 s apart), with near-zero CPU between samples.

## Limits (do not over-generalize)

- One host, one PID, one ten-minute window on Stampede3 SPR.
- Does not measure memory, network, or RabbitMQ broker cost.
- Does not claim overhead for other architectures, GPU-heavy collector builds, or 1 Hz continuous sampling.
- Sample interval configured on the node should be confirmed from site config when citing cadence; this file reports only what `pidstat` observed.

## Suggested citation wording

On a production Stampede3 SPR node (2026-08-14), `pidstat` over ten minutes showed `hpcperfstatsd` sample-window peaks of at most 3.0% of one core and a 0.19% average over the observation window (raw log in this directory).
