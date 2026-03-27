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

Configure selects sources via Automake conditionals; use **`./configure --help`** for options (including **`--enable-all-static`**).

## Key source modules

Small, testable units and daemons are split along these lines (non-exhaustive):

| Area | Files (under `src/`) |
|------|----------------------|
| CLI defaults, argv parsing, heap teardown | `monitor_cli.c`, `monitor_cli.h` (shared literals `monitor_cli_lit_*` with `monitor_daemon.c`). |
| Daemon loop, RMQ send, ring buffer | `monitor_daemon.c`, `monitor_daemon.h` (rate-limited resend `fprintf` when not `DEBUG`). |
| Schema text parsing | `schema_entry_parse.c` (`parse_schema_entry`); `schema.c` builds full schemas for types. |
| Archive header / schema suffix / directive class / marks (file mode) | `stats_file_format.c`, `stats_file_format.h` (`stats_file_classify_header_directive`, `stats_file_fprint_mark_multiline`, …); orchestration in `stats_file.c`. |
| RMQ text payloads | `stats_buffer.c` + `stats_buffer_data_append.c` (persistent AMQP; cached `uname` for header + sample lines; batched rows; declare `syslog` INFO in `DEBUG` only). |

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

See **monitor-static-build-verification** and **global-testing-discipline** in `.cursor/rules/` (or `cursor-rules/` under this package) for project expectations.

## Adding code and tests

- New or extracted C helpers should get **`make check`** coverage; register drivers in **`tests/Makefile.am`** and keep **`monitor_unit_cppflags`** there aligned with **`src/Makefile.am`** `hpcperfstatsd_CPPFLAGS` for preprocessor defines. Rule: **monitor-c-new-function-unittests**.

## Documentation maintenance

When you change **layout**, **build/test steps**, **major modules**, or **consumer-visible output**, update this file and, if the change is test-only, **`tests/README.md`**. Cursor rule: **monitor-readme-maintenance**.
