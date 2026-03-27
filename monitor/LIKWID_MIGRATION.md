# LIKWID PMU Migration

This document tracks LIKWID-based replacement of manual MSR programming paths.

## Compile-Time Architecture Contract

- Unified collector uses compile-time architecture selection:
  - `--with-monitor-arch=intel`
  - `--with-monitor-arch=amd`
  - `--with-monitor-arch=auto` (default)
- Configure exports one compile-time selector to monitor sources:
  - `MONITOR_ARCH_INTEL` or `MONITOR_ARCH_AMD`
- Monitor architecture fan-out is removed from active type registration when
  LIKWID mode is enabled.

## Build And Enable

1. Install LIKWID in local prefix (for example `/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix`).
2. Export local-prefix env:
   - `CPPFLAGS=-I/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/include`
   - `LDFLAGS=-L/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib -L/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64 -Wl,-rpath,/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib -Wl,-rpath,/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64`
   - `PKG_CONFIG_PATH=/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib/pkgconfig:/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64/pkgconfig`
3. Configure monitor in unified LIKWID mode:
   - `../configure --with-monitor-arch=auto --disable-infiniband --disable-mic --disable-gpu --disable-opa`

## Migration Matrix

| Module Family | Status | Strategy |
| --- | --- | --- |
| `intel_pmc3*`, `amd64_pmc.c`, `amd64_df.c` | unified active path | replaced by `cpu_counter_metrics.c` + compile-time arch map |
| `intel_rapl.c`, `amd64_rapl.c` | unchanged in this plan | remains Variorum-backed energy collection |
| `intel_uncore.c`, `intel_*_cbo.c`, `intel_skx_cha.c`, `intel_pcu.c`, `intel_skx_imc.c` | fallback retained | custom uncore/box programming remains native |

## Notes

- Unified PMU type is `cpu_counter_metrics`.
- Runtime fallback is contained inside `cpu_counter_metrics.c` for must-match metrics
  that cannot be read through the LIKWID eventset on the current host.
