# LIKWID PMU migration (Intel)

Intel core and uncore performance counters are collected through **LIKWID PMON**
(`likwid_pmc_adapter.c`, `likwid_uncore_adapter.c`). Native MSR, PCI, and MMIO
programming in legacy `intel_*` collectors has been retired behind thin wrappers.

## Supported generations (SKX+)

Intel uncore collectors and LIKWID uncore profiles target **Skylake-X / Cascade
Lake**, **Ice Lake server**, and **Sapphire Rapids** only.

**Retired (no longer classified or registered):** Sandybridge, Ivybridge,
Haswell, Broadwell — including `intel_x86_pcu` MSR PCU and all
`intel_x86_uncore_*_{snb,ivb,hsw,bdw}` types (IMC, CBO, QPI, HAU, R2PCI).

## CPU detection (`cpuid.c`)

| `processor_t` | CPUID signature |
|---------------|-----------------|
| `CASCADE_LAKE` | `06_55` |
| `SKYLAKE` (client) | `06_4e`, `06_5e` |
| `ICELAKE_SERVER` | `06_6a`, `06_6c` |
| `SAPPHIRE_RAPIDS` | `06_8f` |

Signatures for SNB/IVB/HSW/BDW (`06_2a`, `06_3a`, `06_3c`, `06_3d`, …) return
unsupported (`processor_t`-1).

## Collector mapping

| `st_name` | LIKWID profile | Notes |
|-----------|----------------|-------|
| `host_cpu_hw` | `likwid_arch_eventset_for_processor()` | Auto-disables `intel_x86_pmc_gpr4/8` when active |

`host_cpu_hw` LIKWID notes:

- LIKWID returns **UPPERCASE** event names (`INSTR_RETIRED_ANY`, …). Adapter maps them to schema snake_case via `likwid_pmc_schema_map.c` before `stats_set`; FIXC* is matched case-insensitively. Skip LIKWID invalid sentinel `1ULL<<63`.
- **Sapphire Rapids** eventset matches ICX (`MEM_INST_RETIRED_ALL_*` + `L1D_REPLACEMENT` + FIXC0–2). Do not use SKX-era `MEM_LOAD_UOPS_RETIRED_*` (missing in LIKWID for SPR).
| `intel_x86_rapl` | LIKWID RAPL (`likwid_rapl.c`) | Intel-only begin (`likwid_rapl_is_supported_intel_processor`) |
| `amd_x86_rapl` | LIKWID RAPL (`likwid_rapl.c`) | AMD Zen-only begin (`likwid_rapl_is_supported_amd_processor`) |

RAPL notes:

- LIKWID `power_read(cpu, reg, uint64_t *data)` requires a **64-bit** buffer; energy status is the low 32 bits (`likwid_rapl_energy_status_lo32` → `likwid_rapl_raw_to_mj`).
- Do not enable AMD RAPL/PMC/DF types on Intel (or Intel RAPL on AMD); shared `likwid_rapl_is_supported_processor()` is OR of both vendors and is not used for type begin.
| `intel_x86_uncore_imc_skx` | `IMC_SKX` (`MBOX*` CAS) | Cascade Lake / SKX server |
| `intel_x86_uncore_imc_icx` | `IMC_ICX` (`MDEV*` DDR bytes) | Ice Lake server |
| `intel_x86_uncore_imc_spr` | `IMC_SPR` (`MBOX*` + `HBM*` CAS) | DDR and HBM on same type |
| `intel_x86_uncore_cha_skx` | `CHA_SKX` (`CBOX*` LLC events) | Trimmed CBOX subset from `skylakeX/CACHES.txt` |

Counter-name → device/key mapping is exercised by `test_likwid_uncore_adapter.c`
via `likwid_uncore_adapter_emit_counter()`.

## Init order

`host_cpu_hw` must begin before uncore collectors so `HPMinit` / `perfmon_init`
run first (`stats_registry.c` ordering).
