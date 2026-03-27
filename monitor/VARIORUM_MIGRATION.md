# Variorum Hybrid Migration

This monitor tree now supports a hybrid migration path to Variorum `dev`.

## Build And Enable

1. Install Variorum `dev` into the local prefix used by monitor builds.
2. Export local-prefix flags:
   - `CPPFLAGS=-I/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/include`
   - `LDFLAGS=-L/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib -L/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64 -Wl,-rpath,/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib -Wl,-rpath,/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64`
   - `PKG_CONFIG_PATH=/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib/pkgconfig:/home1/01623/sharrell/hpcperfstats_monitor_dev/.build/prefix/lib64/pkgconfig`
3. Configure monitor with Variorum enabled:
   - `../configure --enable-rabbitmq --enable-variorum --disable-infiniband --disable-mic --disable-gpu --disable-opa`

## Coverage Matrix

| Module Family | Status | Strategy |
| --- | --- | --- |
| `intel_rapl.c` | migrated | direct MSR reads replaced with `variorum_get_energy_json()` parsing |
| `amd64_rapl.c` | migrated | direct MSR reads replaced with `variorum_get_energy_json()` parsing |
| `intel_pmc3*` (`intel_4pmc3.c`, `intel_8pmc3.c`, `intel_knl.c`) | fallback retained | guarded by fallback trace, direct MSR PMU path kept |
| `amd64_pmc.c` | fallback retained | guarded by fallback trace, direct MSR PMU path kept |
| `amd64_df.c` | fallback retained | guarded by fallback trace, direct MSR data-fabric path kept |
| `intel_uncore.c`, `intel_*_cbo.c`, `intel_skx_cha.c`, `intel_pcu.c`, `intel_skx_imc.c` | fallback retained | low-level uncore programming remains on direct MSR/MMIO path |

## Notes

- RAPL output schema remains unchanged (`MSR_*ENERGY*` keys are still emitted).
- Fallback paths are intentional for unsupported low-level counters.
- If Variorum JSON keys differ across versions, update `src/variorum_rapl.c` key mapping logic.
