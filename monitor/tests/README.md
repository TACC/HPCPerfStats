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
| `test_pscanf.c` | `pscanf` (`open`/`read` + `vsscanf`) and `file_fopen_read` |
| `test_schema_parse.c` | `parse_schema_entry` / schema option strings |
| `test_stats_file_format.c` | `stats_file_validate_program_header`, schema suffix formatting |
| `test_monitor_cli.c` | RabbitMQ daemon CLI (`monitor_cli_*`), including `-h` via subprocess |
| `test_monitor_cli_globals.c` | Stub globals for `test_monitor_cli` |
| `test_monitor_configure_help.sh` (from `test_monitor_configure_help.sh.in`) | Regression: `configure --help` mentions `--enable-all-static` |
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

## Adding a test

1. Add `test_foo.c` (and any helpers) under `tests/`.
2. Register the program in `tests/Makefile.am`: `check_PROGRAMS`, `test_foo_SOURCES`, and `test_foo_LDADD = $(LDADD)` if linking like other unit tests.
3. To compile production code from `src/`, list paths as `$(top_srcdir)/src/your_unit.c`.
4. Run `autoreconf -fi` in the monitor root if you change `configure.ac`, then reconfigure and `make check`.

Project rules: **monitor-c-new-function-unittests** and **global-testing-discipline**.
