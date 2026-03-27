# LIKWID PMU Migration

This document tracks LIKWID-based replacement of manual MSR programming paths.

## License Gate

- LIKWID is GPL-3.0. Monitor build now requires explicit acknowledgement:
  - `--enable-likwid --enable-gpl-likwid`
- Without both flags, LIKWID integration is disabled.

## Access Mode Decision

- Selected LIKWID access mode for monitor integration is `ACCESSMODE_PERF`.
- This avoids direct MSR write requirements for the first migration slice and
  aligns better with restricted cluster environments.

## Build And Enable

1. Install LIKWID in local prefix (for example `/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix`).
2. Export local-prefix env:
   - `CPPFLAGS=-I/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/include`
   - `LDFLAGS=-L/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib -L/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64 -Wl,-rpath,/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib -Wl,-rpath,/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64`
   - `PKG_CONFIG_PATH=/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib/pkgconfig:/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64/pkgconfig`
3. Configure monitor with LIKWID:
   - `../configure --enable-rabbitmq --enable-variorum --enable-likwid --enable-gpl-likwid --disable-infiniband --disable-mic --disable-gpu --disable-opa`

## Migration Matrix

| Module Family | Status | Strategy |
| --- | --- | --- |
| `intel_pmc3*` (`intel_4pmc3.c`, `intel_8pmc3.c`, `intel_knl.c`) | partial migration | LIKWID adapter (`likwid_pmc_adapter.c`) is attempted first; direct MSR path remains fallback |
| `intel_rapl.c`, `amd64_rapl.c` | unchanged in this plan | remains Variorum-backed energy collection |
| `amd64_pmc.c`, `amd64_df.c` | fallback retained | direct MSR path retained pending LIKWID event mapping validation |
| `intel_uncore.c`, `intel_*_cbo.c`, `intel_skx_cha.c`, `intel_pcu.c`, `intel_skx_imc.c` | fallback retained | custom uncore/box programming remains native |

## Notes

- First migration slice focuses on Intel PMU family only.
- If LIKWID setup/eventset fails at runtime, monitor automatically falls back to
  existing direct MSR behavior for Intel PMU collection.
