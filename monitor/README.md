# HPCPerfStats monitor

C implementation of the **hpcperfstats** data collector: either a **RabbitMQ daemon** (`hpcperfstatsd`) that streams UTF-8 text payloads to a broker, or a **file-mode client** that appends to local stats archives. Downstream ingestion expects a stable host field and `$`-prefixed rotation lines; the consumer contract is defined by `HPCPerfStats/hpcperfstats/listend.py` (see workspace **monitor-workspace-contract**).

## Directory layout

| Path | Purpose |
|------|---------|
| `src/` | Production C sources and headers (`hpcperfstatsd`, collectors, schema, buffers). |
| `tests/` | `check` targets: C unit drivers, shell regression, `Makefile.am`, `run_tests.sh`. Details: [tests/README.md](tests/README.md). |
| `scripts/` | Static bundle and prefix helpers (`build_static_bundle.sh`, `ensure_dotbuild_prefix_static.sh`). |
| `m4/`, `configure.ac` | Autotools. |
| `README` | Legacy reference for **on-disk stats archive** format (headers, schema lines, record groups). |
| `LIKWID_MIGRATION.md`, `VARIORUM_MIGRATION.md` | Architecture / RAPL migration notes. |

## Build flavors

- **RabbitMQ build** (typical for `hpcperfstatsd`): `monitor.c`, `monitor_cli.c`, `monitor_daemon.c`, `stats_buffer.c`, AMQP + libev + LIKWID as configured.
- **Non–RabbitMQ build**: `main.c`, `stats_file.c`, `stats_file_format.c` — local file client and archive I/O.

Configure selects sources via Automake conditionals; use **`./configure --help`** for options (including **`--enable-all-static`**, **`--enable-legacy-pmcs`** / MIC linkage constraints, **`--enable-metric-profiler`**, and **`--with-metric-profiler-backend={none,ebpf}`**).

## Metric profiler build options

- `--enable-metric-profiler` enables compile-time collector timing instrumentation (per-type `st_collect` and per-metric `stats_set`/`stats_inc` paths).
- `--with-metric-profiler-backend=none` keeps attribution internal (wall vs thread CPU vs inferred wait).
- `--with-metric-profiler-backend=ebpf` enables the backend path that emits host-side attribution signals with each periodic profile report.
- Static bundle integration: when `--with-metric-profiler-backend=ebpf` is passed through `scripts/build_static_bundle.sh`, the script also downloads and builds pinned static `libbpf` into the same `PREFIX` tree.

Profiling output is emitted periodically as `metric-profiler:*` lines and includes aggregated call counts, total/avg/min/max wall time, CPU time, and inferred wait time (`wall-cpu` clamped at zero).

## Key source modules

Small, testable units and daemons are split along these lines (non-exhaustive):

| Area | Files (under `src/`) |
|------|----------------------|
| Stats type registry (sorted `st_name` table for lookup / iteration) | `stats_registry.c`, `stats_registry.h` (optional modules guarded by configure **`MONITOR_WITH_*`** / backend macros — keep aligned with `Makefile.am` **`TYPES`**). |
| Prepare/teardown and sink-facing collect cycles | `stats_runtime.c`, `stats_runtime.h`, `stats_sink.h`. |
| Shared UTF-8 text for archive/buffer rows, schema suffixes, `%` marks | `stats_text_format.c`, `stats_text_format.h`. |
| Long **`configure`/daemon-style options** and argv parsing | `monitor_options.c`, `monitor_options.h`; thin entrypoints `monitor_cli.c`, `monitor_cli.h` (shared literals `monitor_cli_lit_*` with `monitor_daemon.c`). |
| Daemon loop, RMQ send, ring buffer | `monitor_daemon.c`, `monitor_daemon.h` (rate-limited resend `fprintf` when not `DEBUG`; full `$` schema header every 6h and when JOBID unloads to `-`). |
| Schema text parsing | `schema_entry_parse.c` (`parse_schema_entry`); `schema.c` builds full schemas for types. |
| Archive header / schema suffix / directive class / marks (file mode) | `stats_file_format.c`, `stats_file_format.h` (`stats_file_classify_header_directive`, `stats_file_fprint_mark_multiline`, …); orchestration in `stats_file.c`. |
| RMQ text payloads | `stats_buffer.c` + `stats_buffer_data_append.c` (persistent AMQP; cached `uname` for header + sample lines; batched rows; declare `syslog` INFO in `DEBUG` only). |

## Power telemetry (DCGM, RAPL, and interpretation)

- **`cpu_counter_metrics` (DCGM CPU backend on Grace)**: publishes **`DCGM_CPU_POWER_UTIL_W`** and **`DCGM_CPU_POWER_LIMIT_W`** (DCGM fields 1130/1131 on **`DCGM_FE_CPU`**). Values are **per NVIDIA CPU entity** (socket); the monitor repeats the same watts on every **logical CPU row** belonging to that socket. Mapping pairs sorted **Linux `physical_package_id`** values from sysfs with sorted **`DCGM_FE_CPU`** entity IDs when counts match; otherwise these columns stay zero.
- **`nvidia_gpu`**: adds **`sysio_power_usage`** and **`module_power_usage`** when the host engine exposes DCGM fields **1132** and **1133**. **`power_usage`** remains the per-GPU draw. On superchips, **module** / **SysIO** readings can **overlap** GPU and Grace CPU DCGM power—do not add them blindly into a single “node total” without NVIDIA/platform documentation.
- **`nvidia_gpu` profiling splits**: when the DCGM stack supports them, **`tensor_imma_active`** and **`tensor_hmma_active`** mirror **`DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE` / `TENSOR_HMMA_ACTIVE`** (tensor IMMA/HMMA pipe duty cycles, 0–100). IMMA is a **low-precision tensor / FP8-adjacent signal only**, not a dedicated FP8 FLOP counter. **`gpu_flops_rate`** / **`fp_mix`** still use **`tensor_active`** plus FP64/32/16 pipes only, so IMMA/HMMA are **not** folded into the lumped FLOP estimate (avoids double-counting with legacy **`tensor_active`**).
- **`intel_rapl`**: adds **`MSR_PP1_ENERGY_STATUS`** (Intel PP1 / uncore plane, MSR `0x641`) when LIKWID can read the **PP1** RAPL domain. PKG/PP0/DRAM semantics are unchanged.
- **Not in this monitor**: chassis / PSU input (BMC), rack PDU, NIC/DPU, and NVMe device power would need separate collectors or future types (SNMP/Modbus, vendor tools, NVMe-MI, etc.).

## hpcperfstatsd: syscalls and blocking I/O

- **RabbitMQ (blocking)**: Publishing still uses **synchronous** rabbitmq-c calls from **libev timer callbacks**, but connect uses **`amqp_socket_open_noblock`** with a bounded wait, **login/RPC timeouts** (`amqp_set_handshake_timeout`, `amqp_set_rpc_timeout`; enabled by default via **`MONITOR_AMQP_TIMEOUT_CPPFLAGS`** in `src/Makefile.am` and `tests/Makefile.am` for rabbitmq-c 0.9+), **`SO_RCVTIMEO` / `SO_SNDTIMEO`** on the broker socket, and a negotiated **non-zero AMQP heartbeat** so wedged sessions fail faster than kernel-default TCP stalls. Set **`MONITOR_AMQP_TIMEOUT_CPPFLAGS=`** empty when building against headers that lack those APIs. If the broker or path still blocks inside the library, the **whole event loop** waits until the call returns or times out. Mitigations for heavy deployments: offload AMQP to a **worker thread** with a bounded queue, or adopt a **non-blocking** client integrated with `ev_io` (larger change). Tuning broker, network, and payload size helps without code changes.
- **RabbitMQ reconnect pacing**: When the broker is unreachable or publish fails, new **TCP** connect attempts are **rate-limited** with a delay of **max(2s, min(`freq`, 30s))** — so large `freq` does not defer reconnects for many minutes, while short `freq` behavior stays similar and ring-buffer drain in one callback still cannot storm connects. **Sample/payload cadence** remains driven by `freq` in the daemon; only reconnect spacing is capped.
- **Collect path (reduced syscalls)**:
  - `pscanf` uses a **stack read** for small files (e.g. JOBID, sysfs flags) and falls back to heap slurp only when the file does not fit.
  - **`cpu`** keeps one `FILE *` on `/proc/stat` and **`rewind`**s each sample; the stream is closed when collect caches are invalidated.
  - **`net`** caches **up** interface names and **re-scans** `/sys/class/net` every 32 samples (and on SIGHUP / jobid–rotate reset / shutdown) to avoid `opendir`/`readdir` and per-iface `flags` reads every tick.
- **Invalidation**: `stats_buffer_runtime_caches_reset()` (SIGHUP), `monitor_reset_all_stats_types()` (log rotation / jobid-driven re-init), and SIGINT shutdown call `cpu_stats_invalidate_file_caches()` and `net_stats_invalidate_iface_cache()` so cached fds and iface lists are not stale across reconfigure or exit.

## Profiling `hpcperfstatsd`

Use these steps on a **representative host** (same kernel, privilege level, and enabled collectors as production). Run as the same user as the daemon when attaching with `strace`/`perf`, unless you use `sudo` and account for `ptrace` scope (`/proc/sys/kernel/yama/ptrace_scope` on many Linux distros).

### Syscall volume (`strace`)

1. **Summarize syscalls over a window** (good for before/after comparisons on collect-path changes):

   ```bash
   # Attach to a running daemon (replace PID)
   strace -c -p PID -o /tmp/hps_strace.txt
   # Let it run across several sample intervals, then Ctrl+C.
   ```

   Inspect `/tmp/hps_strace.txt` for totals on `open`, `openat`, `read`, `close`, `stat`, `fstat`, `getdents`, `connect`, `write`, `recvfrom`, and similar. Compare two captures taken with the same sample frequency and workload.

2. **Trace only relevant syscalls** (less noise, larger logs per syscall class):

   ```bash
   strace -p PID -e trace=openat,read,close,getdents64 -o /tmp/hps_trace.log
   ```

   Adjust the list for your kernel (`getdents` vs `getdents64`, `open` vs `openat`).

3. **Launch under strace** (when you can start the daemon yourself):

   ```bash
   strace -f -o /tmp/hps_spawn.log /path/to/hpcperfstatsd -d ...
   ```

   `-f` follows children if the process forks (e.g. after `-d` daemonize: attach to the **child** PID or use `-f` and filter the log).

### CPU and hot paths (`perf`)

1. **Lightweight counters** for a few sample intervals while attached:

   ```bash
   perf stat -p PID -- sleep 30
   ```

   Use this to spot high CPU time, cycles, or cache behavior; it does not give C line numbers by itself.

2. **Profile with stack traces** (requires debug symbols; build with `-g`, which the static bundle typically already uses):

   ```bash
   perf record -F 99 -g -p PID -- sleep 60
   perf report
   ```

   Look for time in `st_collect` implementations, `path_collect_*`, `pscanf`, `amqp_*`, and libev. On stripped binaries, install debuginfo or rebuild without stripping for meaningful symbol names.

3. **Permissions**: If `perf record` fails, check `perf_event_paranoid` (e.g. `sysctl kernel.perf_event_paranoid`) or run with appropriate capability; site policy varies.

### Interpreting results

- **High `open`/`read`/`close` counts** per sample interval usually point at **collectors** (`collect.c`, per-type `*_collect` in `src/`, sysfs walks such as `net` or `block`).
- **Blocking** in the libev thread often shows as long gaps in syscall timing or stalls under load; correlate with **RabbitMQ** and network health, since publish/login paths are synchronous (see **hpcperfstatsd: syscalls and blocking I/O** above).
- After code changes, re-run **`make check`** in the static build tree (see **Building and verifying**) and repeat the same `strace -c` / `perf record` procedure so comparisons are apples-to-apples.

## Building and verifying

1. **Canonical static build** (pinned deps + `hpcperfstatsd`): from this directory,

   ```bash
   ./scripts/build_static_bundle.sh
   ```

2. **Tests** (from the **configured build tree root**, e.g. `.build-static`):

   ```bash
   make check
   ```

   Or:

   ```bash
   ./tests/run_tests.sh
   ./tests/run_tests.sh /path/to/build-tree
   ```

   Do not rely on **`make check` only inside `build-tree/src/`**; the Automake `tests/` subdir runs from the top of the build mirror.

3. **Cleanup** (optional after a successful verify pass): from the same build tree root, **`make distclean`** removes generated Makefiles and artifacts unless you need to keep the tree configured.

See **monitor-static-build-verification** and **global-testing-discipline** in `monitor/cursor-rules/` for project expectations.

## Adding code and tests

- New or extracted C helpers should get **`make check`** coverage; register drivers in **`tests/Makefile.am`** and keep **`monitor_unit_cppflags`** there aligned with **`src/Makefile.am`** `hpcperfstatsd_CPPFLAGS` for preprocessor defines. Rule: **monitor-c-new-function-unittests**.

## Documentation maintenance

When you change **layout**, **build/test steps**, **major modules**, or **consumer-visible output**, update this file and, if the change is test-only, **`tests/README.md`**. Cursor rule: **monitor-readme-maintenance**.
