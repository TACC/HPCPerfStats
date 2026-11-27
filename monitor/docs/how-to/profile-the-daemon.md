# Profile the daemon

This page shows monitor developers how to measure the syscall volume and CPU hot paths of a running `hpcperfstatsd`.
Use it when you compare a collect-path change before and after, or look for what makes one collect cycle expensive.

Headline overhead numbers do not come from this page: they come from the release RPM measured by `measurement/bin/run_overhead_profile.sh` (`./scripts/prepare_rpmbuild_dirs.sh --measure-overhead`), described in [measurement/RUNBOOK.md](../../measurement/RUNBOOK.md) section 0.

## Prerequisites

- A representative host: same kernel, privilege level and enabled collectors as production.
- `strace` and `perf` installed.
- The same user as the daemon when you attach, or `sudo` with the `ptrace` scope allowing it (`/proc/sys/kernel/yama/ptrace_scope`).
- Debug symbols for `perf record`: build with `-g` (the static bundle does), or install debuginfo for a stripped binary.

## Count syscalls with strace

To compare syscall volume:

1. Summarize syscalls of the running daemon across several sample intervals, then stop with Ctrl+C:

   ```bash
   strace -c -p <PID> -o /tmp/hps_strace.txt
   ```

   Read the totals for `open`, `openat`, `read`, `close`, `stat`, `fstat`, `getdents`, `connect`, `write` and `recvfrom` in `/tmp/hps_strace.txt`.
   Compare only captures taken with the same sample frequency and workload.

2. Optional: trace only the syscalls you care about (less noise, larger logs per class); adjust the list for your kernel (`getdents` or `getdents64`, `open` or `openat`):

   ```bash
   strace -p <PID> -e trace=openat,read,close,getdents64 \
     -o /tmp/hps_trace.log
   ```

3. Optional: start the daemon under strace when you can launch it yourself:

   ```bash
   strace -f -o /tmp/hps_spawn.log /usr/sbin/hpcperfstatsd -d
   ```

   `-f` follows the child that `-d` daemonizes into; otherwise attach to the child PID.

`<PID>` is the `hpcperfstatsd` process ID (`pgrep -x hpcperfstatsd`).

## Find hot paths with perf

To profile CPU time:

1. Read lightweight counters over a few sample intervals; this shows CPU time, cycles and cache behavior, not source lines:

   ```bash
   perf stat -p <PID> -- sleep 30
   ```

2. Record stack traces and open the report:

   ```bash
   perf record -F 99 -g -p <PID> -- sleep 60
   perf report
   ```

   Look for time in the `st_collect` implementations, `path_collect_*`, `pscanf`, `amqp_*` and libev.

## Verify the result

- High `open` / `read` / `close` counts per sample interval point at collectors (`collect.c`, the per-type `*_collect` functions in `src/`, sysfs walks such as `net` or `block`).
- Blocking in the libev thread shows as long gaps in syscall timing or stalls under load; correlate it with RabbitMQ and network health, because the publish and login paths are synchronous ([About the monitor design](../explanation/design.md)).
- After a code change, run `make check` in the static build tree ([Build the RPM](build-the-rpm.md)), then repeat the same `strace -c` and `perf record` run so the two captures compare like with like.

## Troubleshooting

- `perf record` fails with a permission error: check `sysctl kernel.perf_event_paranoid` or run with the needed capability; site policy varies.
- `perf report` shows hex addresses instead of names: the binary is stripped; install debuginfo or rebuild without stripping.
- `strace -p` shows no collect activity: you attached to the parent of a daemonized process; attach to the child PID.

## Related

- [measurement/RUNBOOK.md](../../measurement/RUNBOOK.md)
- [About the monitor design](../explanation/design.md)
- [Run the correctness campaign](run-correctness-campaign.md)
- [Build the RPM](build-the-rpm.md)
