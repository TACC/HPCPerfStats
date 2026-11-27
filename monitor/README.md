# HPCPerfStats monitor

`hpcperfstatsd` is the C data collector of HPCPerfStats: a RabbitMQ daemon that samples host, network, file-system, GPU and CPU-counter statistics and streams them as UTF-8 text payloads to a broker.
This page is for monitor developers, RPM builders and node operators; it covers the tree, the one build command and where every monitor doc lives.

The consumer contract is `HPCPerfStats/hpcperfstats/listend.py`: it expects a stable host field and `$`-prefixed rotation lines (`monitor-workspace-contract.mdc`).

## Directory layout

| Path                       | Purpose                                                                                                                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/`                     | Production C sources and headers (`hpcperfstatsd`, collectors, schema, buffers).                                                                                                      |
| `tests/`                   | `make check` targets: C unit drivers, shell regressions, `Makefile.am`, `run_tests.sh` ([tests/README.md](tests/README.md)).                                                          |
| `measurement/`             | Node measurement kit (cadence, overhead, jitter): [measurement/RUNBOOK.md](measurement/RUNBOOK.md); the site and pipeline kit is `tests/site_measurement/`.                           |
| `scripts/`                 | Static bundle and prefix helpers (`build_static_bundle.sh`, `ensure_dotbuild_prefix_static.sh`), RPM prepare wrappers, `run_valgrind_check.sh`, `run_cpp_linter.sh`.                  |
| `site/`                    | Per-checkout RPM site overrides: untracked `hpcperfstats.site.conf` (broker, credentials) and its tracked `.example` ([build the RPM](docs/how-to/build-the-rpm.md)).                 |
| `m4/`, `configure.ac`      | Autotools.                                                                                                                                                                            |
| `docs/`                    | Monitor docs (list below).                                                                                                                                                            |
| `python/hpcperfstats_raw/` | Standard-library Python reader for raw monitor files (`python3 -m hpcperfstats_raw summary <FILE>`); tests in `python/tests/`, run by `make check` (`tests/test_raw_data_reader.sh`). |
| `README`                   | Short plain-text pointer to this page.                                                                                                                                                |

## Build and test

Run these from `HPCPerfStats/monitor/`.

To build and test:

1. Build the pinned static dependencies and `hpcperfstatsd`:

   ```bash
   ./scripts/build_static_bundle.sh
   ```

2. Run the tests from the configured build tree root, not from `src/` (the Automake `tests/` subdir runs from the top of the build mirror):

   ```bash
   make -C .build-static check
   ```

   `./tests/run_tests.sh` does the same and takes another build tree as its argument.

3. Optional: after a successful pass, run `make distclean` in the same build tree (`make -C .build-static distclean`).

Foreign `scripts/cross_compile_test.sh` runs only for portable or multi-arch diffs, such as DCGM GPU code or shared `perf_util` ([cross-compile](docs/how-to/cross-compile.md)); the rules `monitor-static-build-verification.mdc`, `monitor-dual-verify-cross-and-static.mdc` and `monitor-valgrind-cpp-linter-gate.mdc` in `cursor-rules/` set which families a change needs.

### Build accelerators

When `ccache` and/or `mold` are installed and a one-line probe works, `build_static_bundle.sh` and `run_valgrind_check.sh` compile through ccache and link with mold (`-B<prefix>/libexec/mold`, which works with EL9 GCC 11 and Clang).
Release builds (`--release` or RPM) keep the system linker unless `HPC_BUNDLE_MOLD=1`; ccache stays on because its output is identical.
`HPC_BUNDLE_CCACHE=0` or `HPC_BUNDLE_MOLD=0` turns either off.
Third-party dependency builds (`--deps-only`) use neither.
The logic is `scripts/lib/build_accel.sh`, tested by `tests/test_build_accel.sh`.

### Plan-close checks

A plan that touched monitor C, tests or scripts also runs, with no git hook:

```bash
./scripts/run_valgrind_check.sh
./scripts/run_cpp_linter.sh
```

Install the clang tools into the workspace `.venv` with `pip install -r tests/requirements-cpp-linter.txt` (on TACC, load the matching Python module first).
Logs go to `test_runs/valgrind-check-*.log` and `test_runs/cpp-linter-*.log`.

### Compiler-warning gate

`scripts/run_warning_gate.sh` is a pre-commit hook in the repository `.pre-commit-config.yaml`, and you can run it by hand:

```bash
./scripts/run_warning_gate.sh
./scripts/run_warning_gate.sh --push
```

The default (pre-commit) mode builds the default x86 config of the daemon at `-O2 -Wall -Wextra -Werror`.
`--push` (pre-push) compiles three configurations with `-Wall -Wextra -Werror`:

- The default x86 config: daemon and every make-check program.
- x86 with `--enable-debug --enable-shared-node-multi-jid`: daemon and make-check programs.
- The aarch64 Grace config (arm backend, shared node, `--enable-ib-mad-dlopen`): daemon only, built with clang `--target=aarch64-linux-gnu` and `ld.lld` against the `cross_compile_test.sh` sysroot and aarch64 prefix; nothing runs.

`--push` then links each configuration at `-O0 -ffunction-sections` with `--gc-sections --print-gc-sections`.
It fails on a non-static function that is unreachable in every configuration that compiles it and not listed in `scripts/unreachable_functions.allowlist`.
A helper that only ARM, DEBUG or shared-node code calls counts as reached; the allowlist holds functions only tests call.
The gate prints allowlist entries that some configuration reaches, or that none defines, as stale: remove a name when you delete or start calling its function.

Builds live in cached `.build-warn/<VARIANT>/` trees (gitignored, never `.build-static`), rebuilt when `configure.ac`, a `Makefile.am`, the compiler, the flags or a prefix changes.
Do not run `make` there by hand: objects built without the gate's flags look up to date to it.
Vendored and prefix headers are `-isystem`.

> [!IMPORTANT]
> The gate skips (exit 0, yellow banner) on a host without Linux, gcc, autoreconf or the static prefix `.build/prefix-static`, so commits from such hosts are not warning-checked.
> Set `HPC_WARN_GATE_REQUIRE=1` to fail instead.

Without clang, `ld.lld`, the aarch64 sysroot or prefix, `--push` still runs the x86 variants and skips only the aarch64 variant and the unreachable-function check, behind the same banner.
`PREFIX`, `ARM_SYSROOT` and `ARM_PREFIX` override the paths.
The test is `tests/test_run_warning_gate.sh`.

## Add code and tests

New or extracted C helpers need `make check` coverage: register the driver in `tests/Makefile.am` and keep `monitor_unit_cppflags` there aligned with the `src/Makefile.am` `hpcperfstatsd_CPPFLAGS` defines (`monitor-c-new-function-unittests.mdc`).

When a change moves the layout, the build or test steps, a major module or consumer-visible output, update this page or the matching page below in the same change; a test-only change updates `tests/README.md` (`monitor-readme-maintenance.mdc`).
When a change alters what the daemon prints (a line kind, banner property, mark, tier token, schema option, sentinel code, or any type, key or device format), update [docs/RAW_DATA_FORMAT.md](docs/RAW_DATA_FORMAT.md), the reader in `python/hpcperfstats_raw/`, `docs/metric_descriptions.tsv` and the generated [docs/METRICS_REFERENCE.md](docs/METRICS_REFERENCE.md) (`scripts/gen_metrics_reference.py`) together; `make check` (`tests/test_raw_data_reader.sh`) fails when they drift (`monitor-raw-data-format-docs.mdc`).

## Documentation

How-to guides:

- [Build the RPM](docs/how-to/build-the-rpm.md): configure options, site conf, fleet and debug RPMs.
- [Deploy to nodes](docs/how-to/deploy-to-nodes.md): rollout order and emergency collector disable.
- [Run a correctness campaign](docs/how-to/run-correctness-campaign.md): validators, fault injection, counter checks.
- [Profile the daemon](docs/how-to/profile-the-daemon.md): syscalls and CPU hot paths.
- [Cross-compile](docs/how-to/cross-compile.md): qemu-user smoke for another CPU family.

Explanation:

- [Design](docs/explanation/design.md): shared-node collection, two-tier sampling, privilege model.
- [x86 counters and roofline](docs/explanation/x86-counters-and-roofline.md): direct perf counters and roofline peaks.

Reference:

- [Source modules](docs/reference/source-modules.md): where each collector and subsystem lives.
- [Legacy formats](docs/reference/legacy-formats.md): decoding archives written by older monitor builds.
- [Raw data format](docs/RAW_DATA_FORMAT.md): how to read the raw stats text and the Python reader.
- [Metrics reference](docs/METRICS_REFERENCE.md): every type and key the daemon can emit, generated from the `KEYS` macros.
- [C refactor checklist](docs/C_REFACTOR_CHECKLIST.md): per-file refactor procedure.
- [Tests](tests/README.md) and the [measurement runbook](measurement/RUNBOOK.md).
