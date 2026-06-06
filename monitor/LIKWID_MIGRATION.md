# LIKWID PMU migration (Intel)

Intel core and uncore performance counters are collected through **LIKWID PMON**
(`likwid_pmc_adapter.c`, `likwid_uncore_adapter.c`). Native MSR, PCI, and MMIO
programming in legacy `intel_*` collectors has been retired behind thin wrappers.

## CPU detection (`cpuid.c`)

| `processor_t` | CPUID signature |
|---------------|-----------------|
| `CASCADE_LAKE` | `06_55` |
| `SKYLAKE` (client) | `06_4e`, `06_5e` |
| `ICELAKE_SERVER` | `06_6a`, `06_6c` |
| `SAPPHIRE_RAPIDS` | `06_8f` |

## Collector mapping

| `st_name` | LIKWID profile | Notes |
|-----------|----------------|-------|
| `host_cpu_hw` | `likwid_arch_eventset_for_processor()` | Auto-disables `intel_x86_pmc_gpr4/8` when active |
| `intel_x86_rapl` | LIKWID RAPL (`likwid_rapl.c`) | Unchanged |
| `intel_x86_uncore_imc_{snb,ivb,hsw,bdw,skx}` | `MBOX*` / `MDEV*` CAS events | Per-MBOX device rows |
| `intel_x86_uncore_imc_icx` | `MDEV*` DDR bytes | Ice Lake server |
| `intel_x86_uncore_imc_spr` | `MBOX*` + `HBM*` CAS | DDR and HBM on same type |
| `intel_x86_uncore_cbo_*`, `cha_skx`, `qpi_*`, `hau_*`, `r2pci_*` | LIKWID adapter | Auto-disable when no LIKWID event profile |

## Init order

`host_cpu_hw` must begin before uncore collectors so `HPMinit` / `perfmon_init`
run first (`stats_registry.c` ordering).
