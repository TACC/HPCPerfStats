# Monitor-originated telemetry variables

This document catalogs **`host_data.event` names** that the HPCPerfStats monitor can publish (aligned with `HPCPerfStats/monitor/src` `KEYS` macros and the generator in `hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py`).

**Regenerating definitions:** run `python3 hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py` to refresh `variableMetadataMonitorEvents.js`.

**Diagnostic bullets** (for events not wired into job metrics/plots) are added by `docs/augment_monitor_variables_diagnostics.py` after this catalog is regenerated.

---

## End-to-end data path

1. **Monitor** (C sources under `HPCPerfStats/monitor/`) samples counters and prints text lines (`t jid host` timestamps plus `type dev values` rows and `!type` schema lines).
2. **`hpcperfstats/listend.py`** (`on_message`) appends payloads under the per-host archive directory (RabbitMQ consumer).
3. **`hpcperfstats/dbload/sync_timedb.py`** (`add_stats_file_to_db`) reads archive files. The `sync_timedb` process rescans the archive directory after each ingest wave and, when nothing is pending, sleeps before scanning again, while still running scheduled daily-archive seal and raw removal on the configured interval. Before appending raw files to a daily `.tar` or deleting them from disk, **`sync_timedb_ingest_readiness`** requires at least one **`host_data`** row for the **hostname token in the file’s first stats timestamp line** (not the archive directory name) with `time` in the **same Unix second** as that line (monitor emits fractional seconds; ingest stores subsecond `time` values). Configurable via **`sync_archive_require_db_head_ingest`** (default on).
4. **`hpcperfstats/dbload/sync_timedb_parsing.py`** (`parse_stats_lines`, `compute_deltas_and_arc`, `EVENTMAPS_BY_TYPE`) parses lines, maps raw PMC encodings to logical event names, collapses multi-GPU rows, and computes `delta` / `arc`.
5. **`hpcperfstats/dbload/io_helpers.py`** (`host_data_instance_from_stats_row`) builds ORM rows.
6. **`hpcperfstats/site/machine/models.py`** (`host_data` model) stores `time`, `host`, `type`, `dev`, `event`, `unit`, `value`, `delta`, `arc`.
7. **Analysis** (`jid_table`, `metrics`, `plot/*`) and **API/SPA** query `host_data` by job window and schema.

---

## Classifications

### By lifecycle stage

| Stage | Role | Primary modules |
|-------|------|-----------------|
| Transport / archive | Receive monitor payloads, write files | `listend.py` |
| Parse & normalize | Decode PMC schema, units, deltas, GPU collapse | `dbload/sync_timedb_parsing.py` |
| Load into DB | Batch insert `host_data` | `dbload/sync_timedb.py` |
| Job window & schema | Distinct `(type, event)` for a job’s hosts/times | `analysis/gen/jid_table.py`, `TypeDetailDataProvider`, `HostDataProvider` |
| Job metrics | Aggregate to `metrics_data` (avg/max/imbalance, etc.) | `analysis/metrics/metrics.py` |
| Summary plots | Time-series subplots per job | `analysis/plot/summaryplot.py`, `summary_metric_descriptions.py` |
| Roofline | DRAM CAS + FLOPs for arithmetic intensity | `analysis/plot/roofline.py`, `roofline_peaks.py` |
| Node power estimate | Combine RAPL / DCGM CPU / GPU power fields | `analysis/gen/node_power_est.py` |
| API & type detail | JSON for job/host/type explorers | `site/machine/api.py` |
| UI tooltips | Human-readable event text | `site/frontend/src/utils/variableMetadata.js` (`getDescriptionForVariable`), `variableMetadataMonitorEvents.js` |

### By monitor `host_data.type` (`stats_type.st_name`)

| `host_data.type` | Monitor source (`.c`) | Default ingest notes |
|------------------|----------------------|----------------------|
| `amd64_df` | `amd64_df.c` | HW map → `MBW_CHANNEL_*` |
| `amd_gpu` | `amd_gpu.c` | Same KEY names as NVIDIA where applicable (`st_name` is `amd_gpu`, not `amd64_gpu`) |
| `amd64_pmc` | `amd64_pmc.c` | HW map → `FLOPS`, branch/stall events |
| `amd64_rapl` | `amd64_rapl.c` | Package energy |
| `arm_imc` | `arm_imc.c` | `CAS_READS` / `CAS_WRITES` |
| `block` | `block.c` | Block sysfs counters |
| `cpu` | `cpu.c` | Per-CPU jiffies |
| `cpu_counter_metrics` | `cpu_counter_metrics.c` | Intel/AMD/ARM paths; DCGM CPU power; synthetic ARM metrics |
| `ib` | `ib.c` | Skipped by default ingest (`exclude_types`) |
| `ib_ext` | `ib_ext.c` | Extended IB counters |
| `ib_sw` | `ib_sw.c` | Skipped by default ingest |
| `intel_4pmc3` | `intel_4pmc3.c` | Same decode map as `intel_8pmc3` |
| `intel_8pmc3` | `intel_8pmc3.c` | FP_ARITH / fixed counters / legacy SSE FLOP proxies |
| `intel_*_imc` | `intel_*_imc.c` | IMC generations → `CAS_READS` / `CAS_WRITES` |
| `intel_knl_mc_dclk` | `intel_knl_mc.c + dbload normalization` | KNL DRAM CAS |
| `intel_skx_cha` | `intel_skx_cha.c` | CHA uncore events (summary arc sum) |
| `intel_rapl` | `intel_rapl.c` | RAPL MSRs |
| `intel_pcu` | `intel_pcu.c` | Package control / uncore |
| `intel_*_cbo`, `intel_*_qpi`, `intel_*_hau`, `intel_*_r2pci` | various `intel_*.c` | Platform uncore (usage varies) |
| `llite` | `llite.c` | Lustre client |
| `lnet` | `lnet.c` | LNET counters |
| `mdc` | `mdc.c` | Lustre MDC stats |
| `mem` | `mem.c` | System memory |
| `mic` | `mic.c` | Xeon Phi aggregate CPU |
| `net` | `net.c` | Ethernet sysfs |
| `nfs` | `nfs.c` | NFS mountstats |
| `numa` | `numa.c` | NUMA hit/miss |
| `nvidia_gpu` | `nvidia_gpu.c` | DCGM GPU metrics |
| `opa` | `opa.c` | Omni-Path |
| `osc` | `osc.c` | Lustre OSC |
| `proc` | `proc.c` | Per-process `/proc` status |
| `ps` | `ps.c` | Skipped by default ingest |
| `roofline_hw_peak` | `roofline_hw_peak.c` | Host-level roofline peak metadata; emitted only on `$` schema/header changeover |
| `sysv_shm` | `sysv_shm.c` | Skipped by default ingest |
| `tmpfs` | `tmpfs.c` | Skipped by default ingest |
| `vfs` | `vfs.c` | Skipped by default ingest |
| `vm` | `vm.c` | VM stats |

Exact `st_name` values are in `HPCPerfStats/monitor/src/*.c` (grep `.st_name`). Some typenames are normalized during dbload (for example KNL memory controller).

### By functional domain (summary)

- **CPU time & load:** `cpu`, `ps`, `mic` types — `user`, `system`, `load_*`, …
- **Core PMC / FLOPs / frequency:** `intel_*pmc3`, `amd64_pmc`, `cpu_counter_metrics` — `FP_ARITH_*`, `FLOPS`, `INST_RETIRED`, `APERF`, `MPERF`, …
- **DRAM bandwidth:** `intel_*_imc`, `intel_knl_mc_dclk`, `arm_imc`, `amd64_df` — `CAS_READS` / `CAS_WRITES`, `MBW_CHANNEL_*`
- **GPU:** `nvidia_gpu`, `amd_gpu` — `gpu_util`, `tensor_active`, `power_usage`, …
- **High-speed fabric:** `ib_ext`, `opa` — `port_*`, `Port*` counters
- **Ethernet / LNET:** `net`, `lnet` — `rx_bytes`, `tx_bytes`, …
- **Local disk:** `block` — `rd_sectors`, `wr_sectors`, …
- **Shared filesystem:** `llite`, `mdc`, `osc`, `nfs` — bytes, ops, Lustre `mds_*` / `ost_*`
- **Memory & NUMA:** `mem`, NUMA meminfo fields on `mem`, `numa`, `vm`
- **Power:** `intel_rapl`, `amd64_rapl`, `cpu_counter_metrics` (`DCGM_CPU_POWER_*`), GPU power fields
- **Process footprint:** `proc` — `VmRSS`, `VmHWM`, …

---

## Universal vs explicit code references

Every event name below is stored in **`host_data.event`** when the monitor emits it (subject to site `exclude_types` / hardware maps). All such rows flow through the **universal pipeline** in the table above through the ORM model.

The **Additional references** subsection per variable lists repository files that contain a **string literal** with that event name (metrics, plots, tests, metadata). It excludes the generated `variableMetadataMonitorEvents.js` blob and the generator script’s description table, so you see *behavioral* references only. Files named `test_*.py` under `analysis/plot/` are unit tests for plotting even when the path does not contain a `tests/` directory; treat them like other test modules when tracing usage.

**PMC note:** `CTL*` / `CTR*` (and some `V*_CTL*` / `V*_CTR*`) names appear in **`!` schema lines** in raw archives; dbload maps them to logical events (for example `INST_RETIRED`) before insert. Those logical names are what appear in `host_data.event` for PMC rows.

---

## Diagnostic guidance (events not wired into analysis)

Many counters are ingested and visible in type-detail / raw `host_data` views but are **not** rolled into default job summary plots or `metrics_data` aggregates. For those, this document adds **Diagnostic guidance**: practical ways operators and performance engineers can use rates (`delta`, `arc`) and cross-metrics checks to explain bottlenecks, faults, or imbalance. Guidance follows common Linux / HPC / fabric practice (kernel docs, vendor counter manuals, and standard wait-state interpretation).

---

## Variable catalog (alphabetical)

### `APERF`

- **Definition:** Actual frequency clock ticks (MSR); with MPERF yields effective CPU frequency.
- **Domain:** CPU cycles / frequency
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/analysis/gen/tests/test_utils_get_type.py; hpcperfstats/site/machine/tests/test_metrics.py

### `ARM_DRAM_BW_BYTES`

- **Definition:** Synthetic cumulative DRAM byte traffic estimate for ARM/DCGM-backed paths (monitor-derived).
- **Domain:** cpu_counter_metrics (Grace / DCGM / synthetic)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/test_roofline_jid_table.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `ARM_EST_FLOPS`

- **Definition:** Synthetic cumulative floating-point work estimate for ARM/DCGM-backed paths (monitor-derived).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/test_roofline_jid_table.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `Active`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `AnonHugePages`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `AnonPages`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `Bounce`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `CAS_READS`

- **Definition:** Memory-controller DRAM CAS read events (Intel uncore IMC or normalized ARM IMC), used for DRAM bandwidth.
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `intel_bdw_imc`, `intel_hsw_imc`, `intel_ivb_imc`, `intel_knl_mc_dclk`, `intel_skx_imc`, `intel_snb_imc`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_roofline_jid_table.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/analysis/gen/tests/test_utils_get_type.py; hpcperfstats/site/frontend/src/utils/variableMetadata.test.js; hpcperfstats/site/machine/tests/test_metrics.py

### `CAS_WRITES`

- **Definition:** Memory-controller DRAM CAS write events.
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `intel_bdw_imc`, `intel_hsw_imc`, `intel_ivb_imc`, `intel_knl_mc_dclk`, `intel_skx_imc`, `intel_snb_imc`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_roofline_jid_table.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/analysis/gen/tests/test_utils_get_type.py; hpcperfstats/site/machine/tests/test_metrics.py

### `CTL0`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL1`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL2`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL3`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL4`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL5`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL6`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTL7`

- **Definition:** Performance event select register (programs the paired general-purpose counter).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR0`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR1`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR2`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR3`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR4`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR5`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR6`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `CTR7`

- **Definition:** General-purpose performance-monitoring counter value; meaning depends on paired CTL programming.
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** These labels exist only in raw `!` schema lines; the database stores decoded event names. For diagnosis, use the decoded counters (for example cache, memory, or uop events) correlated with time and node to localize stalls or contention.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `DCGM_CPU_POWER_LIMIT_W`

- **Definition:** Per-socket CPU power limit from DCGM when exposed (watts).
- **Domain:** cpu_counter_metrics (Grace / DCGM / synthetic)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare against `DCGM_CPU_POWER_UTIL_W` to see headroom to the cap; sustained utilization near limit with performance loss may indicate power-governed frequency.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `DCGM_CPU_POWER_UTIL_W`

- **Definition:** Per-socket CPU power draw from DCGM on Grace/superchip hosts (watts; replicated per logical CPU in that socket).
- **Domain:** cpu_counter_metrics (Grace / DCGM / synthetic)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/node_power_est.py; hpcperfstats/analysis/gen/test_node_power_est.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `DF_CTR0`

- **Definition:** AMD Data Fabric performance counter 0 (or zero-filled placeholder in unified schema).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw Data Fabric counter slots; meaning depends on monitor programming. Use alongside decoded `MBW_CHANNEL_*` rates to validate DRAM traffic or debug counter setup.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `DF_CTR1`

- **Definition:** AMD Data Fabric performance counter 1.
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw Data Fabric counter slots; meaning depends on monitor programming. Use alongside decoded `MBW_CHANNEL_*` rates to validate DRAM traffic or debug counter setup.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `DF_CTR2`

- **Definition:** AMD Data Fabric performance counter 2.
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw Data Fabric counter slots; meaning depends on monitor programming. Use alongside decoded `MBW_CHANNEL_*` rates to validate DRAM traffic or debug counter setup.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `DF_CTR3`

- **Definition:** AMD Data Fabric performance counter 3.
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw Data Fabric counter slots; meaning depends on monitor programming. Use alongside decoded `MBW_CHANNEL_*` rates to validate DRAM traffic or debug counter setup.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `Dirty`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `FIXED_CTR0`

- **Definition:** Intel fixed counter 0 (typically instructions retired).
- **Domain:** CPU cycles / frequency
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `FIXED_CTR1`

- **Definition:** Intel fixed counter 1 (typically unhalted core cycles).
- **Domain:** CPU cycles / frequency
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `FIXED_CTR2`

- **Definition:** Intel fixed counter 2 (typically reference cycles).
- **Domain:** CPU cycles / frequency
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE`

- **Definition:** Intel PMU: 128-bit packed double-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE`

- **Definition:** Intel PMU: 128-bit packed single-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE`

- **Definition:** Intel PMU: 256-bit packed double-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE`

- **Definition:** Intel PMU: 256-bit packed single-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE`

- **Definition:** Intel PMU: 512-bit packed double-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE`

- **Definition:** Intel PMU: 512-bit packed single-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FP_ARITH_INST_RETIRED_SCALAR_DOUBLE`

- **Definition:** Intel PMU: scalar double-precision floating-point arithmetic instructions retired (Intel SDM).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py

### `FP_ARITH_INST_RETIRED_SCALAR_SINGLE`

- **Definition:** Intel PMU: scalar single-precision FP arithmetic instructions retired.
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py

### `FilePages`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `HugePages_Free`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `HugePages_Total`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `INST_RETIRED`

- **Definition:** Instructions retired (fixed counter or MSR alias aligned with IA32_FIXED_CTR0).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/analysis/gen/tests/test_utils_get_type.py; hpcperfstats/site/machine/tests/test_metrics.py

### `Inactive`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MBW_CHANNEL_0`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_hover_tooltips.py

### `MBW_CHANNEL_1`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py

### `MBW_CHANNEL_2`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py

### `MBW_CHANNEL_3`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py

### `MBW_CHANNEL_4`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py

### `MBW_CHANNEL_5`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py

### `MBW_CHANNEL_6`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py

### `MBW_CHANNEL_7`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py

### `MPERF`

- **Definition:** Reference clock ticks while the core is active; pairs with APERF for frequency.
- **Domain:** CPU cycles / frequency
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `MSR_CORE_ENERGY_STAT`

- **Definition:** Intel RAPL-related core energy (platform-specific naming).
- **Domain:** RAPL / energy MSRs
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** RAPL energy counters support power/cap diagnosis: compare package vs DRAM domain trends with workload phases; sudden plateaus may reflect power or thermal limits.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MSR_DRAM_ENERGY_STATUS`

- **Definition:** Intel RAPL MSR: DRAM domain energy status.
- **Domain:** RAPL / energy MSRs
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** RAPL energy counters support power/cap diagnosis: compare package vs DRAM domain trends with workload phases; sudden plateaus may reflect power or thermal limits.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MSR_PKG_ENERGY_STAT`

- **Definition:** Intel RAPL package energy status (alternate MSR naming).
- **Domain:** RAPL / energy MSRs
- **Typical `host_data.type` values:** `amd64_rapl`
- **Application / library code:** hpcperfstats/analysis/gen/node_power_est.py; hpcperfstats/analysis/gen/test_node_power_est.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `MSR_PKG_ENERGY_STATUS`

- **Definition:** Intel RAPL MSR: package energy status (raw units).
- **Domain:** RAPL / energy MSRs
- **Typical `host_data.type` values:** `intel_rapl`
- **Application / library code:** hpcperfstats/analysis/gen/node_power_est.py; hpcperfstats/analysis/gen/test_node_power_est.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `MSR_PP0_ENERGY_STATUS`

- **Definition:** Intel RAPL MSR: PP0 (cores) energy status.
- **Domain:** RAPL / energy MSRs
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** RAPL energy counters support power/cap diagnosis: compare package vs DRAM domain trends with workload phases; sudden plateaus may reflect power or thermal limits.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MSR_PP1_ENERGY_STATUS`

- **Definition:** Intel RAPL MSR: PP1 (uncore/GT when present) energy status.
- **Domain:** RAPL / energy MSRs
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** RAPL energy counters support power/cap diagnosis: compare package vs DRAM domain trends with workload phases; sudden plateaus may reflect power or thermal limits.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `Mapped`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MemFree`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MemTotal`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** `mem`
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `MemUsed`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** `mem`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `NFS_Unstable`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PageTables`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortErrorCounterSummary`

- **Definition:** OPA aggregated port error summary counter.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortMarkFECN`

- **Definition:** OPA FECN marks applied.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortMulticastRcvPkts`

- **Definition:** OPA multicast packets received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortMulticastXmitPkts`

- **Definition:** OPA multicast packets transmitted.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortRcvBECN`

- **Definition:** OPA backward explicit congestion notifications received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `PortRcvBubble`

- **Definition:** OPA bubble / idle counter on receive path.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortRcvData`

- **Definition:** Intel Omni-Path port counter: data received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `PortRcvFECN`

- **Definition:** OPA forward explicit congestion notifications received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `PortRcvPkts`

- **Definition:** OPA packets received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `PortXmitData`

- **Definition:** Intel Omni-Path port counter: data transmitted.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `PortXmitPkts`

- **Definition:** OPA packets transmitted.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `PortXmitTimeCong`

- **Definition:** OPA time spent in congestion-related transmit delay.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortXmitWait`

- **Definition:** OPA wait or stall counter related to credits or arbitration.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py

### `PortXmitWaitData`

- **Definition:** OPA data-volume related wait counter.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `PortXmitWastedBW`

- **Definition:** OPA bandwidth lost to congestion or backpressure.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** FECN/BECN, congestion, and wait-style counters indicate fabric backpressure; compare with application all-to-all or IO bursts. Rising error summaries warrant port cleaning, firmware checks, or topology review.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `READ_ops`

- **Definition:** NFS READ RPC operation count (mountstats).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** `nfs`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `READ_queue`

- **Definition:** NFS READ time queued before transmission (milliseconds).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `READ_rtt`

- **Definition:** NFS READ round-trip time (milliseconds).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `READ_timeouts`

- **Definition:** NFS READ major timeouts.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `SIMD_DOUBLE_256`

- **Definition:** Intel core PMU: retired 256-bit packed double-precision SIMD FP operations (legacy FLOP proxy).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `SSE_DOUBLE_PACKED`

- **Definition:** Intel core PMU: retired SSE/AVX packed double-precision FP operations (legacy FLOP proxy).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `SSE_DOUBLE_SCALAR`

- **Definition:** Intel core PMU: retired SSE/AVX double-precision scalar FP operations (legacy FLOP proxy).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/gen/utils.py; hpcperfstats/analysis/metrics/metrics.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `Slab`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `SwPortCongestion`

- **Definition:** OPA switch congestion indicator.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `Threads`

- **Definition:** Thread count from sampled process status.
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** `proc`
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `Uid`

- **Definition:** User ID of sampled process.
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `V1_CTL0`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTL1`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTL2`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTL3`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTR0`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTR1`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTR2`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V1_CTR3`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTL0`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTL1`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTL2`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTL3`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTR0`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTR1`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTR2`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `V3_CTR3`

- **Definition:** Intel uncore / mesh CHA counter or control register (Skylake-class naming).
- **Domain:** PMC schema / programming (decoded before DB)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Uncore CHA/mesh programming placeholders in schema dumps. After decoding, use the logical uncore events (often cache or IMC-related) to relate LLC traffic, memory behavior, or cross-socket coherence to application phases.
- **Additional references:** *(schema placeholders; logical event names are persisted after `map_hardware_counter_vals` in `dbload/sync_timedb_parsing.py`)*

### `VL15_dropped`

- **Definition:** InfiniBand VL 15 dropped frames.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Physical-layer and VL15 drop counters warrant link quality checks; intermittent spikes often correlate with cable wear or switch port errors.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmData`

- **Definition:** Data segment size (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmExe`

- **Definition:** Executable code size (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmHWM`

- **Definition:** Peak resident set size (kB)âused for memory high-water style metrics.
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** `proc`
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmLck`

- **Definition:** Locked memory (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmLib`

- **Definition:** Shared library mapping size (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmPTE`

- **Definition:** Page table entries footprint (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmPeak`

- **Definition:** Peak virtual memory size (kB) for a sampled process.
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmRSS`

- **Definition:** Current resident set size (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** `proc`
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmSize`

- **Definition:** Current virtual memory size (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmStk`

- **Definition:** Stack size (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `VmSwap`

- **Definition:** Swapped-out anonymous memory (kB).
- **Domain:** Sampled process /proc status
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Process RSS/HWM and thread count expose memory leaks, unexpected fork bombs, or OpenMP oversubscription when sampled against job steps. Sudden `VmSwap` growth flags thrashing.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `WRITE_ops`

- **Definition:** NFS WRITE RPC operation count.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `WRITE_queue`

- **Definition:** NFS WRITE time queued before transmission (milliseconds).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `WRITE_rtt`

- **Definition:** NFS WRITE round-trip time (milliseconds).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `WRITE_timeouts`

- **Definition:** NFS WRITE major timeouts.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `Writeback`

- **Definition:** NUMA-node memory field from /sys/devices/system/node/nodeN/meminfo (kilobytes unless noted).
- **Domain:** NUMA node memory (meminfo fields)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Per-node meminfo fields diagnose memory pressure and reclaim: growing `Dirty`/`Writeback` with high `iowait` suggests flush backlog; `AnonPages` vs `Mapped` shows compute vs file-backed footprint. Compare across NUMA nodes for imbalance.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `alloc_inode`

- **Definition:** Lustre llite: inode allocation operations counted in per-mount `/proc/fs/lustre/llite/*/stats`.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `allocstall`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `bytes_used`

- **Definition:** Tmpfs: bytes used on the tmpfs mount.
- **Domain:** tmpfs
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** tmpfs growth on compute nodes can exhaust RAM-backed `/dev/shm` or job-local buffers; relate to staging or MPI shared-memory use.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `clocks_event_reasons`

- **Definition:** GPU clock throttle reasons bitmask (DCGM on `nvidia_gpu`; same KEY on `amd_gpu` when populated).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `close`

- **Definition:** Lustre llite: `close(2)` operation count per mount (`/proc/fs/lustre/llite/*/stats`).
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/site/machine/tests/test_update_metrics.py

### `collisions`

- **Definition:** Ethernet collision counter (half-duplex legacy).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `counter_select`

- **Definition:** IB MAD extended counter query selector field.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Configuration/metadata for counter queries rather than workload metrics. Use adjacent payload counters (`port_*`) for link health diagnosis.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `create`

- **Definition:** Lustre llite: file create operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `ctxt`

- **Definition:** Context switches (global, from /proc/stat via ps stats type).
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `delay`

- **Definition:** NFS client delay events from /proc/self/mountstats.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dentry_use`

- **Definition:** VFS: directory cache entries in use (approximate from dentry-state).
- **Domain:** VFS
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VFS cache pressure can precede slow path lookups under millions of small files; compare with Lustre metadata rates.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `direct_read`

- **Definition:** NFS direct I/O read bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `direct_write`

- **Definition:** NFS direct I/O write bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `dirty_pages_hits`

- **Definition:** Lustre llite: dirty client-page cache hits (samples line in `/proc/fs/lustre/llite/*/stats`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre dirty-page cache hits/misses on the client; low hit rates with heavy write loads can push more work to OSS and increase observed write latency.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dirty_pages_misses`

- **Definition:** Lustre llite: dirty client-page cache misses; high misses vs hits can mean poor cache reuse.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre dirty-page cache hits/misses on the client; low hit rates with heavy write loads can push more work to OSS and increase observed write latency.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `errors`

- **Definition:** LNET error counter.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `excessive_buffer_overrun_errors`

- **Definition:** InfiniBand port counter: excessive buffer overruns (base IB sysfs counters).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** InfiniBand link state and reliability counters; correlate transitions with job start/end, cable reseats, or switch maintenance windows.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `file_use`

- **Definition:** VFS: open file handles in use (/proc/sys/fs/file-nr).
- **Domain:** VFS
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VFS cache pressure can precede slow path lookups under millions of small files; compare with Lustre metadata rates.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `files_used`

- **Definition:** Tmpfs: number of files in use on the mount.
- **Domain:** tmpfs
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** tmpfs growth on compute nodes can exhaust RAM-backed `/dev/shm` or job-local buffers; relate to staging or MPI shared-memory use.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `flock`

- **Definition:** Lustre llite: advisory `flock` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `fp16_active`

- **Definition:** FP16 pipe activity percent (DCGM PROF on NVIDIA; shared KEY on `amd_gpu`).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `fp32_active`

- **Definition:** FP32 pipe activity percent (DCGM PROF on NVIDIA; shared KEY on `amd_gpu`).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `fp64_active`

- **Definition:** FP64 pipe activity ratio as percent. DCGM PROF on `nvidia_gpu`; same semantic field on `amd_gpu` (wave/SM naming differs).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `fsync`

- **Definition:** Lustre llite: `fsync` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `getattr`

- **Definition:** Lustre llite: getattr / stat-style metadata operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `getxattr`

- **Definition:** Lustre llite: `getxattr` syscall count.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_count`

- **Definition:** GPU device count for the monitor row. DCGM-visible GPU count on `nvidia_gpu`; `amd_gpu` schema documents a stub row count (often 1).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/site/frontend/src/pages/JobDetail.jsx; hpcperfstats/site/machine/api.py; hpcperfstats/site/machine/cache_utils.py
- **Tests:** hpcperfstats/site/machine/tests/test_job_detail_gpu.py

### `gpu_flops`

- **Definition:** Cumulative estimated GPU FLOPs (monitor-integrated model; both GPU types).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/test_roofline_jid_table.py

### `gpu_flops_rate`

- **Definition:** Instantaneous estimated GPU FLOP/s (monitor model; both GPU types expose this KEY).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_io_link_total_bytes`

- **Definition:** NVIDIA-only (`nvidia_gpu` / DCGM): cumulative PCIe plus NVLink bytes from DCGM PROF link counters (not HBM/VRAM traffic). This KEY is not in `amd_gpu.h`; the AMD monitor schema stops at `gpu_mem_total_bytes` plus `gpu_count`.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/roofline.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_roofline_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `gpu_mem_bw_bytes_rate`

- **Definition:** Estimated GPU memory bandwidth (bytes/s) from the monitor model. DCGM-backed on `nvidia_gpu`; `amd_gpu` uses the same schema key.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `gpu_mem_read_bytes`

- **Definition:** Cumulative estimated GPU memory read bytes (model; shared `amd_gpu` / `nvidia_gpu` schema).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_mem_total_bytes`

- **Definition:** Cumulative estimated GPU memory traffic bytes (model; shared schema).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_mem_write_bytes`

- **Definition:** Cumulative estimated GPU memory write bytes (model; shared schema).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_util`

- **Definition:** GPU utilization percent. Populated from DCGM on `nvidia_gpu`; from GPUPerfAPI (or stub zeros) on `amd_gpu`.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** `amd_gpu`, `nvidia_gpu`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_hover_tooltips.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py; hpcperfstats/site/machine/api.py
- **Tests:** hpcperfstats/site/machine/tests/test_job_detail_gpu.py; hpcperfstats/site/machine/tests/test_metrics.py

### `idle`

- **Definition:** Cumulative CPU idle time.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Diagnostic guidance:** Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms (`irq`/`softirq`), and low-priority work (`nice`) from useful compute.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `idle_sum`

- **Definition:** MIC: aggregate idle time.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `in_flight`

- **Definition:** Number of I/O requests currently in flight to the device.
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `inode_permission`

- **Definition:** Lustre llite: inode permission check count (security / ACL path activity).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `inode_use`

- **Definition:** VFS: inodes in use (inode-state).
- **Domain:** VFS
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VFS cache pressure can precede slow path lookups under millions of small files; compare with Lustre metadata rates.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `interleave_hit`

- **Definition:** NUMA interleave policy hits.
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** NUMA allocator and access counters guide process placement: rising `numa_miss` or `other_node` with compute-bound jobs suggests binding or first-touch policy tuning.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `io_ticks`

- **Definition:** Time the disk queue was actively servicing I/O (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ioctl`

- **Definition:** Lustre llite: `ioctl` operation count on this mount.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `iowait`

- **Definition:** Cumulative CPU time waiting for block I/O.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Diagnostic guidance:** Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms (`irq`/`softirq`), and low-priority work (`nice`) from useful compute.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `irq`

- **Definition:** Cumulative CPU time handling hardware interrupts.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Diagnostic guidance:** Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms (`irq`/`softirq`), and low-priority work (`nice`) from useful compute.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `jiffy_counter`

- **Definition:** MIC: jiffy count at query time.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `kswapd_inodesteal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `kswapd_steal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ldlm_cancel`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `link`

- **Definition:** Lustre llite: hard `link` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/site/frontend/src/pages/JobList.test.jsx

### `link_downed`

- **Definition:** InfiniBand port counter: failed link error recoveries (link went down; monitor comment in ib.c).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** InfiniBand link state and reliability counters; correlate transitions with job start/end, cable reseats, or switch maintenance windows.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `link_error_recovery`

- **Definition:** InfiniBand port counter: successful link error recovery events (monitor ib.c).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** InfiniBand link state and reliability counters; correlate transitions with job start/end, cable reseats, or switch maintenance windows.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `listxattr`

- **Definition:** Lustre llite: `listxattr` syscall count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `load_1`

- **Definition:** One-minute load average (monitor often scales by 100; see unit metadata).
- **Domain:** Load average (ps stats type)
- **Typical `host_data.type` values:** `ps`
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `load_15`

- **Definition:** Fifteen-minute load average.
- **Domain:** Load average (ps stats type)
- **Typical `host_data.type` values:** `ps`
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `load_5`

- **Definition:** Five-minute load average.
- **Domain:** Load average (ps stats type)
- **Typical `host_data.type` values:** `ps`
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `local_link_integrity_errors`

- **Definition:** InfiniBand port counter: local link integrity errors (hardware / cable quality).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** InfiniBand link state and reliability counters; correlate transitions with job start/end, cable reseats, or switch maintenance windows.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `local_node`

- **Definition:** Local-node memory accesses (numastat).
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** NUMA allocator and access counters guide process placement: rising `numa_miss` or `other_node` with compute-bound jobs suggests binding or first-touch policy tuning.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `lookup`

- **Definition:** Lustre llite: pathname `lookup` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `mds_close`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mds_getattr`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mds_getattr_lock`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mds_getxattr`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mds_readpage`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mds_statfs`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mds_sync`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_total_mb`

- **Definition:** Total GPU framebuffer memory (MB), device-reported (shared `nvidia_gpu` / `amd_gpu` KEY).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `mem_used`

- **Definition:** System V shared memory: total bytes used across segments.
- **Domain:** SysV shared memory
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** SysV shared memory usage can indicate legacy IPC or shared arrays; unexpected growth may leak segments across job steps.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_used_mb`

- **Definition:** Used GPU framebuffer memory (MB), device-reported (shared `nvidia_gpu` / `amd_gpu` KEY).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `mem_util`

- **Definition:** GPU memory copy/engine utilization percent. DCGM on `nvidia_gpu`; same field exists on `amd_gpu` when the backend is wired.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `mkdir`

- **Definition:** Lustre llite: `mkdir` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `mknod`

- **Definition:** Lustre llite: `mknod` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `mmap`

- **Definition:** Lustre llite: `mmap` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `module_power_usage`

- **Definition:** NVIDIA-only (`nvidia_gpu` / DCGM): module-scope power (watts) on integrated packages. Omitted from `amd_gpu` KEYS.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/node_power_est.py; hpcperfstats/analysis/gen/test_node_power_est.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `msgs_alloc`

- **Definition:** Lustre LNET: messages currently allocated.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `msgs_alloc_max`

- **Definition:** LNET high-water mark of allocated messages.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `multicast`

- **Definition:** Multicast packet counter (receive path; naming varies by driver).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `nice`

- **Definition:** Cumulative CPU time in low-priority user mode.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `nice_sum`

- **Definition:** MIC: aggregate nice time.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `normal_read`

- **Definition:** NFS normal read bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** `nfs`
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `normal_write`

- **Definition:** NFS normal write bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `nr_anon_transparent_hugepages`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `nr_running`

- **Definition:** Runnable tasks on the run queue (ps/global stat).
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `nr_threads`

- **Definition:** Thread count from global /proc/stat-derived ps sample.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `num_cores`

- **Definition:** MIC: number of cores.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `numa_foreign`

- **Definition:** Pages counted as foreign to the allocating node.
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `numa_hit`

- **Definition:** NUMA allocations satisfied on the preferred node (/sys/.../numastat).
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** NUMA allocator and access counters guide process placement: rising `numa_miss` or `other_node` with compute-bound jobs suggests binding or first-touch policy tuning.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `numa_miss`

- **Definition:** NUMA allocations that missed the preferred node (numastat).
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py

### `open`

- **Definition:** Lustre llite: `open(2)` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `osc_read`

- **Definition:** Lustre llite: bytes attributed to OSC read path in llite stats (byte sum field; complements OSC counters).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `osc_write`

- **Definition:** Lustre llite: bytes attributed to OSC write path in llite stats (byte sum field; complements OSC counters).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ost_destroy`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ost_punch`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ost_read`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ost_setattr`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ost_statfs`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ost_write`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `other_node`

- **Definition:** Remote-node memory accesses (numastat).
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py

### `pageoutrun`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgactivate`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgalloc_normal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgdeactivate`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgfault`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgfree`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pginodesteal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgmajfault`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgpgin`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgpgout`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgrefill_normal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgrotated`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgscan_direct_normal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgscan_kswapd_normal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pgsteal_normal`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_multicast_rcv_pkts`

- **Definition:** Multicast packets received (IB extended).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_multicast_xmit_pkts`

- **Definition:** Multicast packets transmitted (IB extended).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_constraint_errors`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_data`

- **Definition:** InfiniBand counter: payload bytes received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** `ib_ext`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `port_rcv_errors`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_packets`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_pkts`

- **Definition:** Packets received (IB counters).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `port_rcv_remote_physical_errors`

- **Definition:** InfiniBand inbound physical-layer error counter.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_switch_relay_errors`

- **Definition:** InfiniBand switch relay errors on received traffic.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_select`

- **Definition:** IB MAD extended counter query selector field (configuration metadata).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Configuration/metadata for counter queries rather than workload metrics. Use adjacent payload counters (`port_*`) for link health diagnosis.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_unicast_rcv_pkts`

- **Definition:** Unicast packets received (IB extended).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_unicast_xmit_pkts`

- **Definition:** Unicast packets transmitted (IB extended).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_constraint_errors`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_data`

- **Definition:** InfiniBand counter: payload bytes transmitted (width/units per IBTA and sysfs docs).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** `ib_ext`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py
- **Tests:** hpcperfstats/site/frontend/src/utils/variableMetadata.test.js

### `port_xmit_discards`

- **Definition:** Packets not transmitted because the port was down or congested.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_packets`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_pkts`

- **Definition:** Packets transmitted (IB counters).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `port_xmit_wait`

- **Definition:** Time waiting for credits or arbitration (vendor-specific units).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `power_usage`

- **Definition:** GPU power draw (watts). DCGM on `nvidia_gpu`; device-reported path on `amd_gpu` when available.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/node_power_est.py; hpcperfstats/analysis/gen/test_node_power_est.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `processes`

- **Definition:** Processes created (forks) from /proc/stat.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pswpin`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pswpout`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rd_ios`

- **Definition:** Completed read I/O operations.
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rd_merges`

- **Definition:** Read requests merged with the in-queue I/O queue.
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rd_sectors`

- **Definition:** Sectors read from the block device (512-byte sectors, Linux sysfs block stat).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** `block`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `rd_ticks`

- **Definition:** Time spent on read I/O (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `read`

- **Definition:** Lustre llite: `read(2)` syscall operation count (volume is in `read_bytes`, not return-value based in llite stats).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `read_bytes`

- **Definition:** Cumulative bytes read on the Lustre client from llite or OSC `/proc/fs/lustre/*/stats` (llite counts requested read size per `read(2)`; OSC aggregates byte totals from OST RPCs).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** `llite`
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/site/machine/api.py
- **Tests:** hpcperfstats/site/frontend/src/utils/variableMetadata.test.js; hpcperfstats/site/machine/tests/test_job_detail_fsio.py

### `readdir`

- **Definition:** Lustre llite: `readdir` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `removexattr`

- **Definition:** Lustre llite: `removexattr` syscall count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `rename`

- **Definition:** Lustre llite: `rename` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `reqs`

- **Definition:** Lustre OSC: sample count taken from `req_waittime` lines in `/proc/fs/lustre/osc/*/stats` (paired with `wait`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rmdir`

- **Definition:** Lustre llite: `rmdir` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `route_bytes`

- **Definition:** LNET routed bytes.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `route_msgs`

- **Definition:** LNET routed messages (routers).
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_bytes`

- **Definition:** Bytes received (Ethernet per-interface sysfs, or another typeâdisambiguate with host_data.type).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** `lnet`, `net`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `rx_bytes_dropped`

- **Definition:** LNET receive bytes dropped.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_compressed`

- **Definition:** Linux netdev: received compressed frames (per-interface sysfs statistics).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_crc_errors`

- **Definition:** Linux netdev: frames received with CRC or FCS errors (physical layer or interference).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_dropped`

- **Definition:** Receive drops (kernel or driver).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_errors`

- **Definition:** Receive errors reported by the driver.
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_fifo_errors`

- **Definition:** Linux netdev: receiver FIFO overrun errors.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_frame_errors`

- **Definition:** Linux netdev: framing errors (misaligned or malformed Ethernet frames).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_length_errors`

- **Definition:** Linux netdev: received frames with invalid length field.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_missed_errors`

- **Definition:** Linux netdev: packets missed by the receiver (often ring buffer exhaustion).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_msgs`

- **Definition:** LNET messages received.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_msgs_dropped`

- **Definition:** LNET receive messages dropped.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_over_errors`

- **Definition:** Linux netdev: receiver overrun errors.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `rx_packets`

- **Definition:** Packets received (per-interface sysfs).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `seek`

- **Definition:** Lustre llite: `seek` operation count.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `segs_used`

- **Definition:** System V shared memory: number of segments in use.
- **Domain:** SysV shared memory
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** SysV shared memory usage can indicate legacy IPC or shared arrays; unexpected growth may leak segments across job steps.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `server_read`

- **Definition:** NFS server-side read bytes (mountstats accounting).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `server_write`

- **Definition:** NFS server-side write bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `setattr`

- **Definition:** Lustre llite: `setattr` metadata updates (mode/owner/size, etc.).
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `setxattr`

- **Definition:** Lustre llite: `setxattr` syscall count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `slabs_scanned`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sm_active`

- **Definition:** SM (or CU) activity percent: DCGM PROF on `nvidia_gpu`; GPUPerfAPI-style on `amd_gpu`.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `sm_occupancy`

- **Definition:** Occupancy percent: DCGM warp occupancy on NVIDIA; AMD uses active-wave occupancy wording in schema.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `softirq`

- **Definition:** Cumulative CPU time in softirq bottom halves.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Diagnostic guidance:** Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms (`irq`/`softirq`), and low-priority work (`nice`) from useful compute.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `statfs`

- **Definition:** Lustre llite: `statfs` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `symbol_error`

- **Definition:** Minor link symbol errors on InfiniBand.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Physical-layer and VL15 drop counters warrant link quality checks; intermittent spikes often correlate with cable wear or switch port errors.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `symlink`

- **Definition:** Lustre llite: `symlink` creation operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `sys_sum`

- **Definition:** MIC: aggregate system time.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sysio_power_usage`

- **Definition:** NVIDIA-only (`nvidia_gpu` / DCGM): SysIO instantaneous power (watts). Not present in the slimmer `amd_gpu` monitor schema.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `system`

- **Definition:** Cumulative CPU time in kernel mode.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/metrics/test_job_for_metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/site/frontend/src/pages/JobDetail.jsx

### `temperature`

- **Definition:** GPU temperature (degrees C). DCGM on `nvidia_gpu`; `amd_gpu` when GPUPerfAPI supplies it.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/sync_timedb_parsing.py

### `tensor_active`

- **Definition:** GPU tensor/matrix pipe activity as percent in monitor output. On `nvidia_gpu` this comes from DCGM PROF; `amd_gpu` shares the same KEY name with a slimmer schema.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** `amd_gpu`, `nvidia_gpu`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/dbload/sync_timedb_parsing.py

### `thp_collapse_alloc`

- **Definition:** Linux /proc/vmstat: successful collapse of page tables into a THP.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Transparent hugepage counters show THP allocation success vs fallback/split; high `thp_fault_fallback` or `thp_split` with memory-bound jobs can mean fragmentation or conflicting `madvise` behavior—compare with `pgmajfault` and RSS from `proc`.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `thp_collapse_alloc_failed`

- **Definition:** Linux /proc/vmstat: failed attempts to collapse mappings into a THP.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Transparent hugepage counters show THP allocation success vs fallback/split; high `thp_fault_fallback` or `thp_split` with memory-bound jobs can mean fragmentation or conflicting `madvise` behavior—compare with `pgmajfault` and RSS from `proc`.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `thp_fault_alloc`

- **Definition:** Linux /proc/vmstat: transparent hugepage allocations satisfied on fault.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Transparent hugepage counters show THP allocation success vs fallback/split; high `thp_fault_fallback` or `thp_split` with memory-bound jobs can mean fragmentation or conflicting `madvise` behavior—compare with `pgmajfault` and RSS from `proc`.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `thp_fault_fallback`

- **Definition:** Linux /proc/vmstat: THP fault handling fell back to small pages.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Transparent hugepage counters show THP allocation success vs fallback/split; high `thp_fault_fallback` or `thp_split` with memory-bound jobs can mean fragmentation or conflicting `madvise` behavior—compare with `pgmajfault` and RSS from `proc`.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `thp_split`

- **Definition:** Linux /proc/vmstat: transparent hugepage splits (e.g. unmap, compaction, or policy).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Transparent hugepage counters show THP allocation success vs fallback/split; high `thp_fault_fallback` or `thp_split` with memory-bound jobs can mean fragmentation or conflicting `madvise` behavior—compare with `pgmajfault` and RSS from `proc`.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `threads_core`

- **Definition:** MIC: hardware threads per core.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `time_in_queue`

- **Definition:** Accumulated time requests spent in the I/O scheduler queue (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `truncate`

- **Definition:** Lustre llite: `truncate` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `tx_aborted_errors`

- **Definition:** Linux netdev: aborted transmissions (driver or hardware abort).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_bytes`

- **Definition:** Bytes transmitted (Ethernet per-interface sysfs, Lustre LNET, or other typeâuse host_data.type).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** `lnet`, `net`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `tx_carrier_errors`

- **Definition:** Linux netdev: loss of carrier during transmit (cable, duplex, or link partner).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_compressed`

- **Definition:** Linux netdev: transmitted compressed frames.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_dropped`

- **Definition:** Transmit drops.
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_errors`

- **Definition:** Transmit errors reported by the driver.
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_fifo_errors`

- **Definition:** Linux netdev: transmit FIFO errors (underrun/overrun, driver dependent).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_heartbeat_errors`

- **Definition:** Linux netdev: heartbeat / half-duplex loss-of-carrier style errors.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_msgs`

- **Definition:** LNET messages transmitted.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_packets`

- **Definition:** Packets transmitted.
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py
- **Tests:** hpcperfstats/site/machine/tests/test_metrics.py

### `tx_window_errors`

- **Definition:** Linux netdev: classic transmitter window errors on outbound frames.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Rising `rx_errors`, `tx_errors`, or drops alongside flat goodput points to driver, cable, or switch issues; packet rate vs byte rate helps distinguish small-message storms from bulk transfers. CRC/fifo/frame errors often indicate bad optics or duplex/speed mismatch. Correlate with MPI or TCP job phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `unlink`

- **Definition:** Lustre llite: `unlink` operation count.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py

### `user`

- **Definition:** Cumulative CPU time in user mode (per-core counter; units per Linux /proc/stat, typically jiffies).
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/metrics/test_per_interval_rate.py; hpcperfstats/analysis/metrics/test_job_for_metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/site/frontend/src/pages/JobDetail.jsx; hpcperfstats/site/frontend/src/pages/JobList.jsx

### `user_sum`

- **Definition:** Intel Xeon Phi (MIC): aggregate user time across cores.
- **Domain:** Intel Xeon Phi (MIC)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Xeon Phi aggregate CPU counters support legacy KNC-style diagnosis: compare aggregated user/system/idle against expected offload utilization.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `wait`

- **Definition:** Lustre OSC: cumulative microseconds from the `req_waittime` sum field in `/proc/fs/lustre/osc/*/stats` (use deltas with `reqs` for average wait in a window).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `wr_ios`

- **Definition:** Completed write I/O operations.
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `wr_merges`

- **Definition:** Write requests merged with the in-queue I/O queue.
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `wr_sectors`

- **Definition:** Sectors written to the block device (512-byte sectors).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** `block`
- **Application / library code:** hpcperfstats/analysis/metrics/metrics.py

### `wr_ticks`

- **Definition:** Time spent on write I/O (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `write`

- **Definition:** Lustre llite: `write(2)` syscall operation count (byte volume in `write_bytes`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `write_bytes`

- **Definition:** Cumulative bytes written on the Lustre client from llite or OSC `/proc/fs/lustre/*/stats`.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** `llite`
- **Application / library code:** hpcperfstats/analysis/gen/jid_table.py; hpcperfstats/analysis/metrics/metrics.py; hpcperfstats/analysis/plot/summaryplot.py; hpcperfstats/analysis/plot/test_summaryplot_jid_table.py; hpcperfstats/site/machine/api.py
- **Tests:** hpcperfstats/site/machine/tests/test_job_detail_fsio.py

### `xprt_bad_xids`

- **Definition:** NFS RPC transport bad XID count.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `xprt_bklog_u`

- **Definition:** NFS transport backlog utilization accumulator.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `xprt_req_u`

- **Definition:** NFS transport accumulated in-flight request measure (kernel xprt stats).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Mountstats `delay` and RPC timing fields expose client-perceived NFS latency; compare timeouts and queue times with server or network events. Asymmetric read/write behavior helps separate metadata from data path problems.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

