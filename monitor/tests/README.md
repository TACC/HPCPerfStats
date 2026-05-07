# HPCPerfStats monitor tests

This directory holds **automated tests** for the monitor package. For package layout, build flavors, and key `src/` modules, see **[../README.md](../README.md)**.

Production sources stay in `../src/`; tests are small drivers that compile and link selected `.c` files from `src/` (see `Makefile.am`).

## Layout

| Artifact | Role |
|----------|------|
| `test_likwid_rapl_scale.c` | RAPL energy scaling helper sanity checks |
| `test_string1.c` | `wsep` / `strsep_ne` (header-only `string1.h`) |
| `test_stats_buffer_data_append.c` | `stats_buffer_data_append` (RMQ payload string growth) |
| `test_path_collect.c` | `path_collect_single` / `path_collect_list` (`open`/`read` path I/O) |
| `test_path_read.c` | `path_read_small` / `path_read_all_alloc` |
| `test_sys_iter.c` | `sys_iter_for_each` (sysfs/sys iteration helper) |
| `test_procfile_parse.c` | `procfile_parse_ws`, `proc_kv_into_stats` |
| `test_msr_io.c` | `msr_open_cpu`, `msr_read_u64` |
| `test_intel_mmconfig.c` | `intel_mmconfig_open` / close (`/dev/mem` mmap probe hooks) |
| `test_monitor_log.c` | `monitor_log_*` facade |
| `test_pscanf.c` | `pscanf` (`open`/`read` + `vsscanf`) and `file_fopen_read` |
| `test_schema_parse.c` | `parse_schema_entry`, `schema_init` / `schema_destroy` (links `schema.c`, `dict.c`) |
| `test_stats_file_format.c` | `stats_file_validate_program_header`, schema suffix formatting |
| `test_stats_text_format.c` | Schema suffix helpers + **`stats_format_append_mark_va`** (mark concatenation) |
| `test_cpu_counter_metrics_schema.c` | `CPU_COUNTER_METRICS_KEYS` includes Grace DCGM power schema tokens |
| `test_dcgm_pkg_uniq.c` | Sorted package-id unique-count logic (maps to DCGM_FE_CPU pairing) |
| `test_gpu_schema_contract.c` | NVIDIA/AMD GPU schema substrings (incl. module/sysio power keys) |
| `test_nfs_schema_subset.c` | NFS collector schema tokens vs. `nfs.c` |
| `test_metric_profiler.c` | Metric profiler aggregation/report smoke test (enabled and disabled builds) |
| `test_monitor_cli.c` | RabbitMQ daemon CLI (`monitor_cli_*`), including `-h` via subprocess |
| `test_monitor_cli_globals.c` | Stub globals for `test_monitor_cli` |
| `test_rabbitmq_integration.sh` | Rootless end-to-end monitor -> RabbitMQ publish validation |
| `scripts/bootstrap_local_rabbitmq.sh` | User-space RabbitMQ bootstrap/start/stop helper |
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

Project rules: **monitor-c-new-function-unittests** and **global-testing-discipline**.
