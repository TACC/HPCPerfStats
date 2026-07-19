# HPCPerfStats monitor tests

This directory holds **automated tests** for the monitor package. For package layout, build flavors, and key `src/` modules, see **[../README.md](../README.md)**.

Production sources stay in `../src/`; tests are small drivers that compile and link selected `.c` files from `src/` (see `Makefile.am`).

## Layout

| Artifact | Role |
|----------|------|
| `test_dict.c` | `dict_*` hash table (init/set/ref/remv/for_each) |
| `test_host_key_alias.c` | `host_key_alias_lookup` / `host_key_alias_emit` |
| `test_schema_emit.c` | `stats_format_emit_*` banner/schema/mark helpers |
| `test_procfile_kv.c` | `proc_kv_into_stats` (header-only) |
| `test_collect_str.c` | `str_collect_key_list` / `str_collect_prefix_key_list` |
| `test_cpu_counter_dcgm_util.c` | DCGM CPU util math (`cpu_counter_metrics_dcgm_util.c`; **DCGM backend only**) |
| `test_cpu_counter_dcgm_publish.c` | DCGM publish/accumulate helpers (**DCGM backend only**) |
| `test_ib_schema_contract.c` | `ib.h` KEYS rename contract |
| `test_host_schema_contract.c` | Host collector schema token contract |
| `test_lustre_schema_contract.c` | Lustre collector schema token contract |
| `test_opa_schema_contract.c` | OPA collector schema token contract (`host_opa.h`) |
| `test_opa_sysfs_map.c` | hfi1 sysfs filename → schema key map |
| `test_opa_mad_backoff.c` | OPA MAD failure backoff |
| `test_ib_hca_is_opa_hfi.c` | HFI vs IB HCA classification |
| `test_opa_lspci_match.c` | Stampede3 Cornelis/OPA lspci fixture match |
| `test_lnet_schema_contract.c` | LNet collector schema token contract |
| `test_likwid_rapl_scale.c` | RAPL energy scaling helper sanity checks |
| `test_string1.c` | `wsep` / `strsep_ne` (header-only `string1.h`) |
| `test_stats_buffer_data_append.c` | `stats_buffer_data_append` (RMQ payload string growth) |
| `test_stats_buffer_uts.c` | `stats_buffer_ensure_uts_cached` / `stats_buffer_uts_cache_reset` |
| `test_stats_buffer_rows.c` | `stats_buffer_append_enabled_type_rows` (tier row assembly into `sf_data`) |
| `test_stats_buffer_collect.c` | `stats_buffer_collect` fast/full tier payloads (`STATS_BUFFER_TEST_SEND_HOOK`; RabbitMQ build) |
| `test_stats_buffer_debug_shm.c` | DEBUG shm write path (`fast`/`full` overwrite, legacy→full) |
| `test_debug_shm_emit_golden.c` | Golden regression for assembled `@fast`/`@full` sample payloads via debug shm (`tests/expected/debug_shm_*.txt`; DEBUG + RabbitMQ) |
| `test_debug_shm_schema_mirror.c` | `schema` shm file + `stats_buffer_debug_shm_schema_wanted` gating (DEBUG) |
| `test_shm_message_correctness.sh` | Python validator on `tests/expected/synthetic_fixture/`; optional slug goldens (exit 77 skip) |
| `scripts/emit_build_capabilities.py` | Writes `monitor-build-capabilities.json` + `capability_slug` (`make capabilities`) |
| `scripts/build_message_expectations.py` | Host probes + shm `schema` → `expectations_<slug>.json` |
| `scripts/validate_shm_messages.py` | Validates shm `schema`/`fast`/`full` against expectations manifest |
| `test_stats_runtime_collect.c` | `stats_runtime_collect_cycle`, `stats_schema_key_active_this_phase` |
| `test_path_collect.c` | `path_collect_single` / `path_collect_list` (`open`/`read` path I/O) |
| `test_path_read.c` | `path_read_small` / `path_read_alloc` (incl. `PATH_READ_ALLOC_MAX`, NULL guards) |
| `test_sys_iter.c` | `sys_iter_for_each` (sysfs/sys iteration helper) |
| `test_procfile_parse.c` | `procfile_parse_ws`, `proc_kv_into_stats` |
| `test_msr_io.c` | `msr_open_cpu`, `msr_read_u64` |
| `test_intel_mmconfig.c` | `intel_mmconfig_close` always; `intel_mmconfig_open` skipped on non-root hosts (see file comment) |
| `test_monitor_log.c` | `monitor_log_*` facade |
| `test_monitor_options_kv.c` | `monitor_options_apply_daemon_conf_kv` (NULL/unknown/empty keys) |
| `test_pscanf.c` | `pscanf` (`open`/`read` + `vsscanf`) and `file_fopen_read` |
| `test_schema_parse.c` | `parse_schema_entry`, `schema_init` / `schema_destroy` (links `schema.c`, `dict.c`) |
| `test_stats_file_format.c` | `stats_file_validate_program_header`, schema suffix formatting |
| `test_stats_file_format_extra.c` | Extended `stats_file_format` NULL/temp-file coverage |
| `test_stats_text_format.c` | Schema suffix helpers + **`stats_format_append_mark_va`** (mark concatenation) |
| `test_ib_port_state.c` | `ib_port_logic_active` / `ib_port_phys_link_up` sysfs line parsers |
| `test_ib_port_collectible.c` | `ib_port_collectible` NULL guard (sysfs path hardcoded) |
| `test_ib_foreach_hca_port.c` | `ib_foreach_hca_port` NULL-callback guard |
| `test_ib_family.c` | `ib_family_disable_all` |
| `test_ib_mad_backoff.c` | `ib_mad_*_collect_cycle_ok` fresh-state allowance (Infiniband build) |
| `test_ib_mad_decode.c` | `ib_mad_ext_decode_counters` with synthetic MAD buffer (Infiniband build) |
| `test_hwdetect_lspci.c` | `hwdetect_invalidate_probe_cache` + probe smoke |
| `test_cpu_counter_metrics_schema.c` | `CPU_COUNTER_METRICS_KEYS` includes Grace DCGM power schema tokens |
| `test_dcgm_pkg_uniq.c` | Sorted package-id unique-count logic (maps to DCGM_FE_CPU pairing) |
| `test_gpu_schema_contract.c` | NVIDIA/AMD GPU schema substrings (incl. module/sysio power keys) |
| `test_nvidia_gpu_estimate.c` | `nvidia_gpu_estimate_rates` FLOP/memory-bandwidth math |
| `test_roofline_hw_peak_schema.c` | Roofline peak schema contract strings |
| `test_roofline_hw_peak_changeover.c` | Roofline changeover / mode gating with `HPCPERFSTATS_SKIP_HW_PROBE` |
| `test_roofline_detect_fixture.c` | `roofline_hw_peak_detect_fill_cache` fixture when `HPCPERFSTATS_SKIP_HW_PROBE=1` |
| `test_daemonize.c` | `daemonize()` double-fork and PID lock file (subprocess tests) |
| `test_nfs_schema_subset.c` | NFS collector schema tokens vs. `nfs.c` |
| `test_metric_profiler.c` | Metric profiler aggregation/report smoke test (enabled and disabled builds) |
| `test_monitor_cli.c` | RabbitMQ daemon CLI (`monitor_cli_*`), usage sentinel strings, `-h` via subprocess |
| `test_ring_buffer.c` | Ring buffer / `stats_buffer_collect` tier-row assembly (`STATS_BUFFER_TEST_SEND_HOOK`; RabbitMQ build) |
| `test_monitor_cli_globals.c` | Stub globals for `test_monitor_cli` |
| `test_rabbitmq_integration.sh` | Rootless end-to-end monitor -> RabbitMQ publish validation |
| `scripts/bootstrap_local_rabbitmq.sh` | User-space RabbitMQ bootstrap/start/stop helper (must be in the source tree for `make dist` / RPM prepare) |
| `rmq_integration_validate.py` | Consumes queue messages and validates listend-compatible payload shape |
| `requirements-rabbitmq-integration.txt` | Python dependency pin for integration validator (`pika`) |
| `test_monitor_configure_help.sh.in` | Regression (via `check-local`): `configure --help` mentions `--enable-all-static`, `--enable-legacy-pmcs`, `--enable-metric-profiler`, and `--with-metric-profiler-backend` |
| `Makefile.am` | Automake `check_PROGRAMS` / `TESTS`; **keep `monitor_unit_cppflags` in sync** with `src/Makefile.am` `hpcperfstatsd_CPPFLAGS` for `-D` flags |
| `run_tests.sh` | Convenience wrapper around `make check` in a build directory |
| `../scripts/profile_hpcperfstatsd_example.sh` | Prints `perf record` / `perf stat` recipes for CPU baseline comparisons |

## How to run

**Recommended (full build + tests):**

```bash
./scripts/build_static_bundle.sh
make -C .build-static check
```

**Using the runner** (defaults to `.build-static` under the monitor package):

```bash
./tests/run_tests.sh
./tests/run_tests.sh /path/to/your/build-tree
```

**After `configure` + `make` in a build directory:**

```bash
make check
```

Run from the **configured build tree root** (not only `build/src`). The suite lives in the `tests/` subdir of the build mirror.

## RabbitMQ integration test (rootless, no Docker)

This test path installs/starts RabbitMQ in user space, runs `hpcperfstatsd` against it, then validates consumed messages for:

- UTF-8 decodability
- listend-compatible host parsing (`$` payload host token and sample payload third token)
- AMQP publish properties (`content_type=text/plain`, `delivery_mode=2`)

Run explicitly:

```bash
make -C .build-static check-rabbitmq-integration
```

Or run with `check-local` by opting in:

```bash
RUN_RMQ_INTEGRATION=1 make -C .build-static check
```

### Debug shm golden regeneration

When intentional changes alter sparse row assembly or tier tokens, refresh the checked-in expected files:

```bash
UPDATE_DEBUG_SHM_GOLDEN=1 make -C .build-static check TESTS=test_debug_shm_emit_golden
```

Requires a **DEBUG** configure (`--enable-debug`). Non-DEBUG builds skip this test.

Environment knobs:

- `INSTALL_ROOT` (default `~/.cache/hpcperfstats-rmq`) for downloaded/built Erlang + RabbitMQ artifacts.
- `ERLANG_HOME` and `RABBITMQ_HOME` to reuse preinstalled user-space trees and skip downloads/build.
- `SKIP_DOWNLOAD=1` to forbid network fetches (requires local archives or provided homes).
- `ERLANG_ARCHIVE_SHA256` / `RABBITMQ_ARCHIVE_SHA256` for archive integrity checks.
- `MIN_MESSAGES`, `VALIDATE_TIMEOUT_SECONDS`, `MONITOR_WARMUP_SECONDS` to tune runtime/strictness.

## Adding a test

1. Add `test_foo.c` (and any helpers) under `tests/`.
2. Register the program in `tests/Makefile.am`: `check_PROGRAMS`, `test_foo_SOURCES`, and `test_foo_LDADD = $(LDADD)` if linking like other unit tests.
3. To compile production code from `src/`, list paths as `$(top_srcdir)/src/your_unit.c`.
4. Run `autoreconf -fi` in the monitor root if you change `configure.ac`, then reconfigure and `make check`.

Project rules: **monitor-c-new-function-unittests**, **monitor-c-testing-standards**, and **global-testing-discipline**.

## Writing unit tests

Follow **monitor-c-testing-standards** (`.cursor/rules/monitor-c-testing-standards.mdc`):

- **Default to unit tests** — one concern per `test_<module>.c`, link production `.c` from `src/`.
- **F.I.R.S.T.** — fast, independent, repeatable, self-validating (`assert` + non-zero exit), timely (same change as production).
- **AAA** — Arrange → Act → Assert in each `static void test_*()` helper; reset globals in `main()` between groups.
- **Cover contracts** — return values, NUL termination, emitted keys/strings; at least one NULL/bounds and one error path per exported function.
- **Stub `stats_set`** — use `test_stats_stub.c` / `test_stats_stub_bind()` when linking units that emit stats without full `stats.c`.
- **Register** every driver in `Makefile.am` and this layout table.

## Configure-sliced tests

Some drivers are omitted or skipped depending on the configured build:

| Condition | Affected tests |
|-----------|----------------|
| `CPU_BACKEND_DCGM` | `test_cpu_counter_dcgm_util`, `test_cpu_counter_dcgm_publish`, `test_dcgm_pkg_uniq` |
| `LIKWID` | `test_likwid_rapl_scale` links production `likwid_rapl.c`; otherwise inline fallback |
| `RABBITMQ` | `test_monitor_cli`, `test_ring_buffer`, `test_stats_buffer_collect`, `test_stats_buffer_debug_shm`, `test_debug_shm_emit_golden`, `test_monitor_timing` |
| `DEBUG` | `test_stats_buffer_debug_shm`, `test_debug_shm_emit_golden` (meaningful assertions; otherwise skipped) |
| `INFINIBAND` | `test_ib_mad_backoff`, `test_ib_mad_decode` |
| Root / `/dev/mem` | `test_intel_mmconfig` skips open probe when not root (see file comment) |
| Live broker | `test_rabbitmq_integration.sh` — opt-in via `RUN_RMQ_INTEGRATION=1` or `check-rabbitmq-integration` |
