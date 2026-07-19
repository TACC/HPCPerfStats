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

Configure selects sources via Automake conditionals; use **`./configure --help`** for options (including **`--enable-all-static`**, **`--enable-legacy-pmcs`**, **`--enable-metric-profiler`**, **`--with-metric-profiler-backend={none,ebpf}`**, **`--enable-opa`**, and **`--enable-intel-gpu`**).

### Omni-Path / Cornelis (`host_opa`)

- **`host_opa` is always built.** It collects Cornelis CN5000 and Intel Omni-Path HFI 100 Series devices (`hfi1_*` under `/sys/class/infiniband`). Device ids use a slash (`hfi1_0/1`), not the IB dot form.
- **Sysfs fallback (default):** reads overlapping utilization counters from `ports/N/counters` (verified on Stampede3 OPA100 + CN5000). Maps `port_*_packets` → schema `*_pkts`; missing files (e.g. CN5000 without `multicast_*`) are skipped. STL-only keys (FECN/BECN, bubbles, …) stay empty without MAD. HFI `hw_counters` are not mapped into KEYS.
- **Collectible ports only:** inactive/DOWN ports are skipped (`ib_port_collectible`) — e.g. CN5000 dual-port with port1 Offline emits only `hfi1_0/2`.
- **`--enable-opa`:** links Cornelis/Intel IFS **`liboib_utils`** (+ `oib_utils.h`) for STL Performance MAD (full KEYS). Requires IFS devel packages on the build host. `scripts/build_static_bundle.sh` probes for `liboib_utils` and passes `--enable-opa` when the link probe succeeds.
- **`host_ib` never claims `hfi1_*` HCAs** — those belong to `host_opa` only.
- Stampede3 PCI examples: Cornelis CN5000 HFI (SPR); Intel Omni-Path HFI Silicon 100 Series (ICX/SKX/H100).

### Intel Data Center GPU / PVC (`intel_gpu`)

- **`--enable-intel-gpu={auto,yes,no}`** (default **auto** via `scripts/gpu_lspci_probe.sh intel`). Compiles against vendored **`third_party/intel-xpum/`** headers (XPUM **1.2.33** only — not system `/usr/include`).
- Runtime **`dlopen`** of `libxpum` (`/usr/lib64/libxpum.so`, `libxpum.so.1`, …); override with **`HPCPERFSTATS_XPUM_LIB`**. No link-time `-lxpum`.
- Stampede3 PVC: four Data Center GPU Max 1550 (`[8086:0bd5]` / Ponte Vecchio) → device rows `"0"`…`"3"`; keys align conceptually with `nvidia_gpu` / `amd_gpu` (Xe Link keys, not NvLink). Level Zero pipe metrics are deferred.
- Force enable for testing: **`HPCPERFSTATS_FORCE_INTEL_GPU`**. Static bundle enables intel_gpu when vendored headers are present (fleet RPM).

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
| DEBUG shm mirror (`@fast`/`@full` snapshots) | `stats_buffer_debug_shm.c`, `stats_buffer_debug_shm.h` (`DEBUG` builds only). |
| Intel CPUID / generation gating | `cpuid.c`, `intel_cpuid_match.c`, `intel_processor.c` |
| LIKWID core + uncore PMU | `likwid_pmc_adapter.c`, `likwid_uncore_adapter.c`, `likwid_uncore_profiles.c` |
| Omni-Path / Cornelis HFI (`host_opa`) | `opa.c`, `opa_sysfs.c`, `opa_mad_backoff.c`, `host_opa.h` (sysfs always; STL MAD with `--enable-opa`) |
| Intel Data Center GPU / PVC (`intel_gpu`) | `intel_gpu.c`, `intel_gpu.h`, `xpum_gpu_dyn.c` (vendored `third_party/intel-xpum/`; runtime `libxpum` dlopen) |
| IB vs HFI routing | `ib_common.c` (`ib_hca_is_opa_hfi`), `ib_family.c` |

Intel PMU collection uses **LIKWID only** on x86 (see [LIKWID_MIGRATION.md](LIKWID_MIGRATION.md)).
**Uncore collectors (IMC/CHA)** target SKX+ server parts only: Cascade Lake
(`06_55`), Ice Lake server (`06_6a`/`06_6c`), and Sapphire Rapids (`06_8f`).
Sandybridge, Ivybridge, Haswell, and Broadwell are no longer classified or
registered (`intel_x86_pcu` and pre-SKX uncore types removed). SPR exposes DDR
and HBM uncore keys (`dram_*`, `hbm_*`); the SPR IMC LIKWID eventset is chosen
at daemon start from EDAC memory topology (DDR-only, HBM-only, or both), with
fallback if PMU setup fails. Shm validation probes (`probe_spr_imc_devices`) use
the same EDAC rules. `host_roofline_peak` adds
`cpu_peak_hbm_bw_bytes_per_s` (EDAC HBM/DDR split, `peak_calc_version` 2).

## Two-tier collection (fast/slow) and sparse rows

The daemon supports a two-tier sampling scheme to reduce the volume of data sent
without losing freshness on performance metrics. It is **on by default**
(`enable_slow_tier 1`). Set `enable_slow_tier 0` to restore legacy single-tier
behavior (every key collected and emitted full-width every `sample_freq`).

| Config key | Default | Role |
|-----------|---------|------|
| `sample_freq` | `30` | Fast-tier collection interval (performance keys). |
| `sample_freq_slow` | `600` | Full-collection interval; clamped to be `>= sample_freq`. |
| `send_freq` | `300` | RabbitMQ drain interval (unchanged, independent of the tiers). |
| `enable_slow_tier` | `1` | Master switch for tiering and sparse rows. |

When `enable_slow_tier 1` (default):

- **Key tiers** come from a static `(type, key)` table plus an auto-rule for any
  key ending in `_error`/`_errors` (`collect_tier.c`). Slow keys carry a new
  `,R=S` suffix on `!` schema lines so consumers can learn tier membership;
  fast keys are unmarked. Use `R=S` directly in a `KEYS` macro to mark a key slow
  at the schema level.
- **Fast samples** (every `sample_freq`) collect and emit only fast-tier keys.
- **Full samples** (every `sample_freq_slow`) collect and emit every key.
- **Sample rows** gain a tier marker right after the device field:
  - `type dev @fast v...` — fast-tier values only, in schema order.
  - `type dev @full v...` — all values, in schema order.
  - `type dev v...` (no `@` token) — legacy full-width row (slow tier disabled).

### Invariant: `$` messages are always full

Any payload whose first non-whitespace byte is `$` (schema/rotation message) is
**always emitted full**: the schema block lists every key (with `,R=S` on slow
keys) and the appended sample rows use `@full`, never `@fast`. This is enforced
in `stats_buffer_row_tier_decide()` (`stats_buffer_rows.c`) and by forcing
`COLLECT_FULL` on every `write_hdr=1` collect in `monitor_daemon.c`, preserving
the `current`-file rotation semantics that `listend.py` relies on.

### Consumer rollout

`@fast`/`@full` sample rows and the `,R=S` schema suffix are a **new monitor
output contract**. Downstream ingestion in
`HPCPerfStats/hpcperfstats/dbload/lib/sync_timedb_parsing.py` now strips tier
markers and zips values against the fast vs full schema subset. Deploy monitor
and consumer updates together on clusters that ingest sparse rows.

Set `enable_slow_tier 0` only as an escape hatch for sites still running an
older consumer stack (restores legacy full-width rows with no `@` tokens).

Collection gating skips slow-key `stats_set`/`stats_inc` on fast ticks when the
slow tier is enabled, reducing host CPU as well as wire volume.

### IB driver merge (`host_ib`)

The former `host_ib_ext` and `host_ib_sw` typenames are merged into **`host_ib`**
with a single sysfs walk per cycle. Migration notes for historical archives:

| Legacy | Current |
|--------|---------|
| `host_ib_ext` / `host_ib_sw` typename | `host_ib` |
| Device id `mlx5_0/1` | `mlx5_0.1` |
| Switch keys `rx_bytes`, `tx_bytes`, … | `sw_rx_bytes`, `sw_tx_bytes`, … |

See `HPCPerfStats/docs/monitor_variable_rename_map.yaml` for rename-map entries.

### DEBUG `/dev/shm` mirror (daemon only)

When built with **`--enable-debug`**, the RabbitMQ daemon mirrors the latest **full
outbound payloads** under **`/dev/shm/hpcperfstatsd-debug/`**:

| File | Content |
|------|---------|
| `schema` | Complete `$` rotation payload (`write_hdr=1`) |
| `fast` | Latest `@fast` sample |
| `full` | Latest `@full` (or legacy full-width) sample |

Override the base directory with **`HPCPERFSTATS_DEBUG_SHM_DIR`**. Payloads
contain job id, hostname, and workload metrics — treat as sensitive on shared
nodes. Files are created mode `0600` under a `0700` directory (atomic `*.tmp` +
`rename`).

**RPM debug path** (symbols + behavioral DEBUG for `/dev/shm`):

```bash
cd HPCPerfStats/monitor
./scripts/prepare_rpmbuild_dirs.sh --debug-build
# Run the printed debug rpmbuild line (hpc_debug_build 1), then copy/paste the
# verification runbook footer (manifest + validate_shm_messages.py).
```

The footer uses paths under `rpmbuild/BUILD/hpcperfstats-<ver>/.build-static/` from
the RPM `%build` tree. After install, `hpcperfstats.spec` `%post` starts
`hpcperfstats.service`; shm
payloads appear under `/dev/shm/hpcperfstatsd-debug/` (override with
`HPCPERFSTATS_DEBUG_SHM_DIR`).

The default **release** `rpmbuild` (no `hpc_debug_build`) strips the binary and
passes `--disable-debug` — no `/dev/shm` mirror. Compiler `-g` alone does not
enable the mirror; the spec sets `HPC_BUNDLE_ENABLE_DEBUG=1` only for
`hpc_debug_build` RPMs.

### `/dev/shm` message correctness testing

On a data host with a DEBUG build (static bundle or debug RPM):

```bash
cd HPCPerfStats/monitor
./scripts/build_static_bundle.sh --enable-debug   # or debug RPM via prepare --debug-build
make -C .build-static capabilities
# start hpcperfstatsd; wait for schema + fast + full updates under /dev/shm/...
SLUG=$(python3 -c "import json; print(json.load(open('.build-static/monitor-build-capabilities.json'))['capability_slug'])")
python3 scripts/build_message_expectations.py \
  --capabilities .build-static/monitor-build-capabilities.json \
  --shm-dir /dev/shm/hpcperfstatsd-debug \
  --enable-slow-tier 1 \
  --out ".build-static/expectations_${SLUG}.json"
python3 scripts/validate_shm_messages.py \
  --capabilities .build-static/monitor-build-capabilities.json \
  --manifest ".build-static/expectations_${SLUG}.json" \
  --shm-dir /dev/shm/hpcperfstatsd-debug \
  --live-spot-check \
  --report "../../test_runs/monitor/validate_${SLUG}_$(date +%F).txt"
```

Validation layers: structural (schema keys, row width, tier markers, uint values,
manifest device IDs, listend host contract), plausibility warnings
(`--strict-plausibility` to fail), live `/proc`/`/sys` spot checks on the
data host (`--live-spot-check`, default on live shm; `--no-live-spot-check` for
fixtures), and optional **cross-sample** checks (`--cross-sample-check`:
timestamp cadence vs active conf + monotonic `E` counters across two snapshots).
Debug RPM installs `sample_freq=30` and `sample_freq_slow=60` in
`/etc/hpcperfstats/hpcperfstats.conf` (see `hpcperfstats.spec` `hpc_debug_build`
block); cross-sample wait bounds are derived from that file at validate time.
Optional emit drift check: copy shm files to
`tests/expected/shm_{schema,fast,full}_<slug>.txt` and pass
`--golden-dir tests/expected`.

For **debug RPM**, use `./scripts/prepare_rpmbuild_dirs.sh --debug-build` — it prints a
single chained command:

```bash
rpmbuild -ba ... hpc_debug_build 1 ... && ./scripts/rpm_debug_shm_verify.sh
```

Run from `HPCPerfStats/monitor/`. No exports needed. Re-validate only:
`SKIP_INSTALL=1 ./scripts/rpm_debug_shm_verify.sh`

Cross-sample monotonic/cadence verify (debug RPM 30s/60s conf):

```bash
CROSS_SAMPLE_CHECK=1 ./scripts/rpm_debug_shm_verify.sh
```

**Capability slug** — `monitor-build-capabilities.json` includes
`capability_slug` (compile flags + `slowtier0`/`slowtier1`). Golden fixtures
and expectations must use the same slug in filenames
(`tests/expected/shm_*_<slug>.txt`, `expectations_<slug>.json`). CI runs
`tests/test_shm_message_correctness.sh` (synthetic fixture always; live slug
goldens when present; **exit 77 skip** otherwise). Local run logs belong under
**`<workspace-root>/test_runs/`** (see **test-runs-output-directory**).

## Power telemetry (DCGM, RAPL, and interpretation)

- **`cpu_counter_metrics` (DCGM CPU backend on Grace)**: publishes **`DCGM_CPU_POWER_UTIL_W`** and **`DCGM_CPU_POWER_LIMIT_W`** (DCGM fields 1130/1131 on **`DCGM_FE_CPU`**). Values are **per NVIDIA CPU entity** (socket); the monitor repeats the same watts on every **logical CPU row** belonging to that socket. Mapping pairs sorted **Linux `physical_package_id`** values from sysfs with sorted **`DCGM_FE_CPU`** entity IDs when counts match; otherwise these columns stay zero.
- **`nvidia_gpu`**: adds **`sysio_power_usage`** and **`module_power_usage`** when the host engine exposes DCGM fields **1132** and **1133**. **`power_usage`** remains the per-GPU draw. On superchips, **module** / **SysIO** readings can **overlap** GPU and Grace CPU DCGM power—do not add them blindly into a single “node total” without NVIDIA/platform documentation.
- **`nvidia_gpu` profiling splits**: when the DCGM stack supports them, **`tensor_imma_active`**, **`tensor_hmma_active`**, and **`tensor_dfma_active`** mirror **`DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE` / `TENSOR_HMMA_ACTIVE` / `TENSOR_DFMA_ACTIVE`** (tensor pipe duty cycles, 0–100). IMMA is a **low-precision tensor / FP8-adjacent signal only**, not a dedicated FP8 FLOP counter. **`gpu_flops_rate`** / **`fp_mix`** still use **`tensor_active`** plus FP64/32/16 pipes only, so IMMA/HMMA/DFMA are **not** folded into the lumped FLOP estimate (avoids double-counting with legacy **`tensor_active`**). If **`dcgmWatchFields`** rejects optional tensor split field IDs (common on older embedded DCGM builds), the monitor automatically retries with the smaller core PROF field set so **`nvidia_gpu`** rows still publish on GH/Hopper clusters.
- **`nvidia_gpu` DCGM extras**: per-direction link counters (**`gpu_pcie_*_bytes`**, **`gpu_nvlink_*_bytes`**, schema suffix **`E`** — monotonic hardware counters), **`gpu_mem_free_mb`**, **`gpu_sm_clock`**, **`gpu_pcie_replay_counter`**, and **`gpu_dram_active`** when the active watch profile includes those fields. **`gpu_io_link_total_bytes`** is a separate delta-accumulated event counter built from those PROF byte fields.
- **DCGM watch fallback ladder** for `nvidia_gpu`: `full-prof` (IMMA/HMMA/DFMA) → `core-prof` (legacy FP/SM/tensor/DRAM pipes) → `basic-nonprof` (power/temp/util/fb/clock/replay). This prevents complete row loss when DCGM profiling fields are unsupported or permissioned off; PROF-derived values become zero while base GPU rows continue emitting.
- **Hardware sniff (`lspci`) gaps**: optional **`nvidia_gpu`** stays enabled when **`lspci`** is unavailable (prior behavior). When **`lspci`** is present but omits vendor prose, **`/proc/driver/nvidia/version`**, **`/dev/nvidia0`**, or **`lspci -nn`** lines matching **`[10de:`** plus usual GPU PCI classes still defeat disabling **`nvidia_gpu`** before DCGM runs. PCI class/vendor heuristics are shared between runtime (`src/gpu_pci_detect.c` / `hwdetect.c`) and build/configure probes (`scripts/gpu_lspci_probe.sh`); **`tests/test_gpu_lspci_detect_parity.sh`** guards parity on fixture lines.
- Runtime override: set **`HPCPERFSTATS_FORCE_NVIDIA_GPU=1`** to keep `nvidia_gpu` enabled even if runtime hardware sniffing misses NVIDIA.
- **x86 fleet RPM (LIKWID CPU)**: the monitor is built with vendored DCGM headers and **loads `libdcgm` at runtime** (`dlopen`) when `nvidia_gpu` initializes — CPU-only nodes do not need `libdcgm` installed and the binary does not link it at build time. GPU nodes need a compatible **`libdcgm.so`** (override with **`HPCPERFSTATS_DCGM_LIB`**) and a working DCGM host engine (embedded or remote) as today. **aarch64 / Grace** builds still link **`libdcgm`** at compile time for the DCGM CPU backend.
- **`roofline_hw_peak` in daemon payloads**: emitted **only** when **`stats_wr_hdr()`** runs (`write_hdr=1`): same buffered payload as the **`$`** property banner / **`!`** schema lines from **`stats_wr_hdr`**, followed by the first **`stats_buffer_collect`** timestamp block (`roofline_hw_peak_collect` is gated by **`stats_collect_on_changeover`**). **SIGHUP** reload triggers an immediate header/sample collect so schema changes are visible without waiting for the periodic rotate timer.
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

## Cross-compile smoke (qemu-user)

Use `scripts/cross_compile_test.sh` when you want one machine to run compile+test smoke for multiple target triplets.

- **Native triplet** (`target` CPU family matches host): the script runs the canonical native path (`scripts/build_static_bundle.sh` then `make check`) without qemu wrapping for compilation.
- **Foreign triplet** (`target` CPU family differs from host): the script uses host `make`/`cmake`/`pkg-config` and qemu-wrapped target compiler/binutils from a target sysroot (`gcc`, `g++`, `ar`, `ranlib`, `strip` under `SYSROOT/usr/bin`).
- If `SYSROOT`/`SYSROOT_<TRIPLET>` is not set, the script auto-creates a **Rocky 9** sysroot under `.build/sysroots/` by downloading an official Rocky container-base rootfs tarball and extracting it in user space (`AUTO_CREATE_ROCKY9_SYSROOT=1`, default).
- When the base rootfs lacks compiler/binutils, the script programmatically layers a Rocky **Container-Toolbox** rootfs tarball over the same sysroot to provide `gcc/g++/ar/ranlib/strip` in user space.
- If the toolbox layer still lacks compiler binaries, the script performs a rootless RPM payload fallback: it downloads selected Rocky toolchain RPMs and unpacks them into the sysroot via `rpm2cpio|cpio` (no root, no package install transaction).
- Fully explicit mode: set `ROOTFS_TARBALL` or `ROOTFS_TARBALL_<TRIPLET_SLUG>` to use your own rootfs tarball.
- If qemu user emulators are missing, the script bootstraps a local qemu-user build under `.build/qemu-local` (`LOCAL_QEMU_ROOT`).
- Rootless mode is the only mode: no `sudo`, no `podman/docker`, no `conmon/runc`, and no `binfmt_misc` registration.
- The script keys behavior from the **configured triplet**, not `uname -m`, because user-mode emulation can still report host architecture.
- Foreign monitor builds pass **`--disable-all-static`** so tests link against the sysroot’s dynamic **`libc`/`libm`** (full **`--enable-all-static`** against glibc tends to fail under qemu-user smoke due to static **`libm`** IFUNC resolvers). Native triplets still use the canonical static-bundle flow.
- **`make check`** for foreign triplets runs test binaries under **qemu-user** via Automake **`LOG_COMPILER`**; **`QEMU_LD_PREFIX`** (already set by the script from **`SYSROOT`**) resolves target **`ld-linux`** / **`libc`** for emulation—same semantics as **`qemu-* -L SYSROOT`**.
- For deterministic foreign smoke, monitor configure also forces:
  **`--disable-gpu --disable-amd-gpu --disable-infiniband --disable-opa --disable-lustre`**.

Foreign prerequisites:

1. Install host build tooling: `make`, `cmake`, `pkg-config`, plus downloader (`curl` or `wget`).
   QEMU source bootstrap may also install Python `tomli` and `ninja` in user space via `python3 -m pip --user`.
2. Provide a sysroot per target triplet, or allow script auto-generation via Rocky rootfs tarball download.
3. For non-x86 targets, ensure target-arch `libdcgm` and headers are available in the sysroot (DCGM CPU backend requirement in `configure.ac`).
4. Export one of:
   - `SYSROOT_<TRIPLET_SLUG>` (preferred), e.g. `SYSROOT_AARCH64_LINUX_GNU=/opt/sysroots/aarch64`
   - `SYSROOT` as a fallback for all foreign targets.
5. For automatic Rocky rootfs generation, ensure the host can download:
   `https://download.rockylinux.org/pub/rocky/<release>/images/<arch>/Rocky-<release>-Container-Base.latest.<arch>.tar.xz`
6. For automatic toolchain layering, ensure the host can also download:
   `https://download.rockylinux.org/pub/rocky/<release>/images/<arch>/Rocky-<release>-Container-Toolbox.latest.<arch>.tar.xz`
7. For RPM fallback layering, ensure host `rpm2cpio` and `cpio` are available.

Example:

```bash
cd HPCPerfStats/monitor
TARGETS="x86_64-linux-gnu aarch64-linux-gnu riscv64-linux-gnu" \
SYSROOT_AARCH64_LINUX_GNU="/opt/sysroots/aarch64" \
SYSROOT_RISCV64_LINUX_GNU="/opt/sysroots/riscv64" \
./scripts/cross_compile_test.sh --fail-fast
```

If deps are already cached per target prefix, add `--skip-deps`.

Automatic Rocky 9 rootfs download + sysroot extraction example:

```bash
cd HPCPerfStats/monitor
TARGETS="x86_64-linux-gnu" \
AUTO_CREATE_ROCKY9_SYSROOT=1 \
./scripts/cross_compile_test.sh --force-foreign --fail-fast
```

Explicit rootfs tarball example:

```bash
cd HPCPerfStats/monitor
TARGETS="x86_64-linux-gnu" \
ROOTFS_TARBALL_X86_64_LINUX_GNU="/path/to/rocky9-x86_64-rootfs.tar.xz" \
AUTO_CREATE_ROCKY9_SYSROOT=0 \
./scripts/cross_compile_test.sh --force-foreign --fail-fast
```

## Adding code and tests

- New or extracted C helpers should get **`make check`** coverage; register drivers in **`tests/Makefile.am`** and keep **`monitor_unit_cppflags`** there aligned with **`src/Makefile.am`** `hpcperfstatsd_CPPFLAGS` for preprocessor defines. Rule: **monitor-c-new-function-unittests**.

## Documentation maintenance

When you change **layout**, **build/test steps**, **major modules**, or **consumer-visible output**, update this file and, if the change is test-only, **`tests/README.md`**. Cursor rule: **monitor-readme-maintenance**.
