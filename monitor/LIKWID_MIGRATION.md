# LIKWID PMU migration (Intel + AMD EPYC)

Intel core and uncore performance counters are collected through **LIKWID PMON**
(`likwid_pmc_adapter.c`, `likwid_uncore_adapter.c`). Native MSR, PCI, and MMIO
programming in legacy `intel_*` collectors has been retired behind thin wrappers.

## AMD EPYC (Rome → Turin) — LIKWID only, no MSR fallback

| `st_name` | LIKWID profile | CPUID |
|-----------|----------------|-------|
| `host_cpu_hw` | `likwid_arch_eventset_for_processor(AMD_*)` | Rome/Milan/Genoa/Turin |
| `amd_x86_uncore_df_rome` | `DF_ROME` (`DRAM_CHANNEL_*:DFC*`) | Fam17h Models 30h–3Fh |
| `amd_x86_uncore_df_milan` | `DF_MILAN` | Fam19h Models 00h–0Fh |
| `amd_x86_uncore_df_genoa` | `DF_GENOA` (`DRAM_READS_LOCAL_CHANNEL_*:DFC*`) | Fam19h 10h–1Fh / A0h–AFh |
| `amd_x86_uncore_df_turin` | `DF_TURIN` (`CAS_CMD_RD:UMC*C0`) | Fam1Ah Models 00h–1Fh |
| `amd_x86_rapl` | LIKWID RAPL | same EPYC enums |

**Deleted (no compile/link, no runtime fallback):** `amd64_pmc.c`, native MSR `amd64_df`, event tables / `amd64_pmu_core`, emit types `amd_x86_pmc` and `amd_x86_uncore_df`. If LIKWID setup fails on EPYC, `host_cpu_hw` and DF types stay disabled.

EPYC-only CPUID allowlists live in `amd_cpuid_match.c` (Naples / Ryzen models outside those ranges return unsupported).

## Intel `host_cpu_hw` — LIKWID only (no MSR / no gpr*)

On SKX / CLX / ICX / SPR / EMR / GNR / SRF, LIKWID owns core PMCs. **Deleted:** `intel_4pmc3` /
`intel_8pmc3` (`intel_x86_pmc_gpr4` / `gpr8`), `msr_io`, and `fallback_fill`.
If LIKWID setup fails, `host_cpu_hw` is **disabled** (same fail-closed policy
as EPYC). Util/clock columns in `host_cpu_hw` stay 0 (Grace/DCGM only). FIXC
may be all-zero on **idle** logical CPUs after warm-up; waking the CPU
(affinity busyloop) makes FIXC advance. Debug shm census: CPU id is `$2` when
the row includes `@full`; FIXC are `$10–$12`.

## Supported Intel generations (SKX+)

Intel LIKWID collectors target server generations from **Skylake-X** through
**Sierra Forest** (pinned LIKWID 5.5.2): SKX, CLX, ICX, SPR, EMR, GNR, SRF.

**Retired (no longer classified or registered):** Sandybridge, Ivybridge,
Haswell, Broadwell — including `intel_x86_pcu` MSR PCU and all
`intel_x86_uncore_*_{snb,ivb,hsw,bdw}` types (IMC, CBO, QPI, HAU, R2PCI).

## CPU detection (`cpuid.c`)

| `processor_t` | CPUID signature |
|---------------|-----------------|
| `SKYLAKE` (client) | `06_4e`, `06_5e` |
| `SKYLAKE_X` | `06_55` stepping &lt; 5 (LIKWID `skylakeX`) |
| `CASCADE_LAKE` (CLX; Cooper Lake) | `06_55` stepping ≥ 5 (LIKWID `CLX`) |
| `ICELAKE_SERVER` | `06_6a`, `06_6c` |
| `SAPPHIRE_RAPIDS` | `06_8f` |
| `EMERALD_RAPIDS` | `06_cf` |
| `GRANITE_RAPIDS` | `06_ad` |
| `SIERRA_FOREST` | `06_af` |

Signatures for SNB/IVB/HSW/BDW (`06_2a`, `06_3a`, `06_3c`, `06_3d`, …) return
unsupported (`processor_t`-1). Diamond Rapids and later are out of scope until
LIKWID pin adds them.

## Collector mapping

| `st_name` | LIKWID profile | Notes |
|-----------|----------------|-------|
| `host_cpu_hw` | `likwid_arch_eventset_for_processor()` | LIKWID-only; no MSR / gpr* fallback |
| `intel_x86_rapl` | LIKWID RAPL (`likwid_rapl.c`) | Intel-only begin (`likwid_rapl_is_supported_intel_processor`) |
| `amd_x86_rapl` | LIKWID RAPL (`likwid_rapl.c`) | AMD Zen-only begin (`likwid_rapl_is_supported_amd_processor`) |
| `intel_x86_uncore_imc_skx` | `IMC_SKX` (`MBOX*` CAS) | SKX + CLX server |
| `intel_x86_uncore_imc_icx` | `IMC_ICX` (`CAS_COUNT_*:MBOX0–11` → `mdevN`) | Ice Lake server; PERF kernel `cas_count_*` on `uncore_imc_*` (not `DDR_*:MDEV*`) |
| `intel_x86_uncore_imc_spr` | `IMC_SPR` (`MBOX*` + `HBM*` CAS) | DDR and HBM on same type |
| `intel_x86_uncore_imc_emr` | `IMC_EMR` (SPR event ladder) | Emerald Rapids |
| `intel_x86_uncore_imc_gnr` | `IMC_GNR` (`CAS_COUNT_SCH0_*`) | Granite Rapids |
| `intel_x86_uncore_imc_srf` | `IMC_SRF` (`CAS_COUNT_SCH0_*`) | Sierra Forest |

`host_cpu_hw` LIKWID notes:

- LIKWID returns **UPPERCASE** event names (`INSTR_RETIRED_ANY`, …). Adapter maps them to schema snake_case via `likwid_pmc_schema_map.c` before `stats_set`; FIXC* is matched case-insensitively. Skip LIKWID invalid sentinel `1ULL<<63`.
- **Sapphire Rapids / Emerald Rapids / Granite Rapids** core eventset matches ICX (`MEM_INST_RETIRED_ALL_*` + `L1D_REPLACEMENT` + FIXC0–2). Do not use SKX-era `MEM_LOAD_UOPS_RETIRED_*` (missing in LIKWID for SPR).
- **Sierra Forest** uses Atom-server names (`MEM_LOAD_UOPS_RETIRED_L{1,2,3}_HIT` + FIXC); no `L1D_REPLACEMENT` in LIKWID SRF tables.
- **GNR/SRF IMC** must use `CAS_COUNT_SCH0_RD`/`WR` (not SPR `CAS_COUNT_RD`).

RAPL notes:

- **No DIRECT MSR:** `likwid_pmc_adapter_init` always uses `ACCESSMODE_PERF`. `HPCPERFSTATS_LIKWID_ACCESS=direct` is rejected (journal warn) and does not enable `power_read`.
- **Pinned LIKWID must be built `ACCESSMODE=perf_event`** (`build_static_bundle.sh` / `cross_compile_test.sh`). Runtime PERF on a DIRECT-built liblikwid leaves `access_init` NULL → `perfmon_init` ENODEV (no `host_cpu_hw`).
- **RAPL collect:** LIKWID **PWR*** perfmon (`likwid_rapl_pwr.c`, e.g. `PWR_PKG_ENERGY:PWR0`) via the `power` PMU. **Select domains from** `/sys/bus/event_source/devices/power/events/` (`energy-pkg`→PKG, `energy-ram`→DRAM, `energy-cores`→PP0, `energy-gpu`→PP1) **before** `addEventSet` — Stampede3 ICX has pkg+ram only; programming PP0/PP1 causes journal `Invalid argument` while `setupCounters` still returns 0. Quiet stderr for both add and setup. If PWR results are flat zero/NaN (common when counters only land on the socket-lock thread or energy units stay unset), collect falls back to **`/sys/class/powercap/*/energy_uj`** (`rapl_powercap.c`) — still no MSR **0x38f**. Do not call `power_read`.
- **ICX IMC under PERF:** use `CAS_COUNT_RD/WR:MBOX*` (ladder MBOX12→6→4), map to devices **`mdevN`** / keys `dram_cas_*`. Do not use `DDR_READ_BYTES:MDEV*` (fails eventset setup on Stampede3 ICX).
- Do not enable AMD RAPL/PMC/DF types on Intel (or Intel RAPL on AMD); shared `likwid_rapl_is_supported_processor()` is OR of both vendors and is not used for type begin.
- **`intel_x86_rapl` and `amd_x86_rapl` begin require `cpu_counter_metrics_likwid_ready()`** and `likwid_rapl_pwr_begin()` (PWR eventset and/or powercap available); if that fails the type is **disabled** — do not publish flat-zero rows.
- **Flat-zero `core_energy` / `pkg_energy` on AMD is not healthy idle behavior** — it means energy collect failed or RAPL was never initialized (typically `host_cpu_hw` / HPMinit did not run). Healthy sockets show large cumulative mJ.
- **RAPL vendor path is runtime** (`likwid_rapl_collect_path`): EPYC uses AMD **PWR** strings even when the binary was configured with `MONITOR_ARCH_INTEL`.
- AMD core eventset uses **`LS_DISPATCH_ALL`** (LIKWID Zen umask); bare `LS_DISPATCH` fails `perfmon_addEventSet` and silently used to disable `host_cpu_hw`.
- **Multi-group PERF collect:** after DF/IMC/RAPL `perfmon_setupCounters` on later groups, each `host_cpu_hw` tick calls `likwid_pmc_adapter_prepare_collect()` → `perfmon_setupCounters(g_group)` then `(void)perfmon_startCounters()` (uncore finish pattern; ignore start failure if already running) then `readGroupCounters(g_group)`. Setup alone left Turin/SPR flat after deploy. Judge AMD health on mid-row `retired_instructions` / `ls_dispatch` / `instr_retired` under load (not Intel FIXC/util).
- **`LIKWID_FORCE`:** privileged host daemon defaults `LIKWID_FORCE=1` via `likwid_pmc_adapter_ensure_force_env()` before `HPMinit` (same effect as `likwid-perfctr -f`). Without force, LIKWID refuses in-use PMC0–PMC3 (`addEventSet` −22). Opt out with `LIKWID_FORCE=0`. Quiet setup (`HPCPERFSTATS_LIKWID_SETUP_QUIET`, default on) retries failed add/setup/start once with stderr restored so “in use” lines reach the journal.
- **`HPCPERFSTATS_LIKWID_ACCESS`:** unset / empty / `perf` → `ACCESSMODE_PERF` (only supported mode). `direct` is **removed** (journal error; still PERF). Invalid values also fall back to `perf`. Journal line: `LIKWID HPM access mode: perf`.
- `host_cpu_hw` begin failures log init vs eventset step and the event string (`likwid_backend_begin` / `likwid_pmc_adapter_setup_events`); `perfmon_init` failures include errno.

Counter-name → device/key mapping is exercised by `test_likwid_uncore_adapter.c`
via `likwid_uncore_adapter_emit_counter()`.

## Init order

`host_cpu_hw` must begin before LIKWID uncore collectors and RAPL so `HPMinit` /
`perfmon_init` run first. The stats registry stays sorted by `st_name` for binary
search (`stats_registry.h`); AMD DF and `amd_x86_rapl` sort **before** `host_cpu_hw`
alphabetically. **`stats_runtime.c`** therefore uses a two-phase begin: init all
enabled types, then call `host_cpu_hw` `st_begin` before every other type's begin.
Intel IMC types already sort after `host_cpu_hw` and are unchanged.
