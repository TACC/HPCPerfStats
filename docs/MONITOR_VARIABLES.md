# Monitor-originated telemetry variables

This document catalogs **`host_data.event` names** that the HPCPerfStats monitor can publish (aligned with `HPCPerfStats/monitor/src` `KEYS` macros and the generator in `hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py`).

**Regenerating definitions:** run `python3 hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py` to refresh `variableMetadataMonitorEvents.ts`.

**Diagnostic bullets** (for events not wired into job metrics/plots) are added by `docs/augment_monitor_variables_diagnostics.py` after this catalog is regenerated.

---

## End-to-end data path

1. **Monitor** (C sources under `HPCPerfStats/monitor/`) samples counters and prints text lines (`t jid host` timestamps plus `type dev values` rows and `!type` schema lines).
2. **`hpcperfstats/listend.py`** (`on_message`) appends payloads under the per-host archive directory (RabbitMQ consumer).
3. **`hpcperfstats/dbload/sync_timedb.py`** (`add_stats_file_to_db`) reads archive files.
4. **`hpcperfstats/dbload/lib/sync_timedb_parsing.py`** (`parse_stats_lines`, `compute_deltas_and_arc`, `EVENTMAPS_BY_TYPE`) parses lines, maps raw PMC encodings to logical event names, collapses multi-GPU rows, and computes `delta` / `arc`.
5. **`hpcperfstats/dbload/io_helpers.py`** (`host_data_instance_from_stats_row`) builds ORM rows.
6. **`hpcperfstats/site/lib/machine/models.py`** (`host_data` model) stores `time`, `host`, `type`, `dev`, `event`, `unit`, `value`, `delta`, `arc`.
7. **Analysis** (`jid_table`, `metrics`, `plot/*`) and **API/SPA** query `host_data` by job window and schema.

---

## Classifications

### By lifecycle stage

| Stage | Role | Primary modules |
|-------|------|-----------------|
| Transport / archive | Receive monitor payloads, write files | `listend.py` |
| Parse & normalize | Decode PMC schema, units, deltas, GPU collapse | `dbload/lib/sync_timedb_parsing.py` |
| Load into DB | Batch insert `host_data` | `dbload/sync_timedb.py` |
| Job window & schema | Distinct `(type, event)` for a job’s hosts/times | `analysis/metrics/lib/gen/jid_table.py`, `TypeDetailDataProvider`, `HostDataProvider` |
| Job metrics | Aggregate to `metrics_data` (avg/max/imbalance, etc.) | `analysis/metrics/lib/metrics.py` |
| Summary plots | Time-series subplots per job | `analysis/metrics/lib/plot/summaryplot.py`, `summary_metric_descriptions.py` |
| Roofline | DRAM CAS + FLOPs for arithmetic intensity | `analysis/metrics/lib/plot/roofline.py`, `roofline_peaks.py` |
| Node power estimate | Combine RAPL / DCGM CPU / GPU power fields | `analysis/metrics/lib/gen/node_power_est.py` |
| API & type detail | JSON for job/host/type explorers | `site/lib/machine/api.py` |
| UI tooltips | Human-readable event text | `site/frontend/src/utils/variableMetadata.js` (`getDescriptionForVariable`), `variableMetadataMonitorEvents.ts` |

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
| `host_ib` | `ib.c` | Unified sysfs + MAD + switch IB counters (ingested) |
| `intel_4pmc3` | `intel_4pmc3.c` | Same decode map as `intel_8pmc3` |
| `intel_8pmc3` | `intel_8pmc3.c` | FP_ARITH / fixed counters / legacy SSE FLOP proxies |
| `intel_*_imc` | `intel_*_imc.c` | IMC generations → `CAS_READS` / `CAS_WRITES` |
| `intel_skx_cha` | `intel_skx_cha.c` | CHA uncore events (summary arc sum) |
| `intel_rapl` | `intel_rapl.c` | RAPL MSRs |
| `intel_pcu` | `intel_pcu.c` | Package control / uncore |
| `intel_*_cbo`, `intel_*_qpi`, `intel_*_hau`, `intel_*_r2pci` | various `intel_*.c` | Platform uncore (usage varies) |
| `llite` | `llite.c` | Lustre client |
| `lnet` | `lnet.c` | LNET counters |
| `mdc` | `mdc.c` | Lustre MDC stats |
| `mem` | `mem.c` | System memory |
| `net` | `net.c` | Ethernet sysfs |
| `nfs` | `nfs.c` | NFS mountstats |
| `numa` | `numa.c` | NUMA hit/miss |
| `nvidia_gpu` | `nvidia_gpu.c` | DCGM GPU metrics |
| `opa` | `opa.c` | Omni-Path |
| `osc` | `osc.c` | Lustre OSC |
| `proc` | `proc.c` | Per-process `/proc` status |
| `ps` | `ps.c` | Skipped by default ingest |
| `sysv_shm` | `sysv_shm.c` | Skipped by default ingest |
| `tmpfs` | `tmpfs.c` | Skipped by default ingest |
| `vfs` | `vfs.c` | Skipped by default ingest |
| `vm` | `vm.c` | VM stats |

Exact `st_name` values are in `HPCPerfStats/monitor/src/*.c` (grep `.st_name`). Historical archives may still use legacy KNL typenames decoded in `sync_timedb_parsing_legacy.py`.

### By functional domain (summary)

- **CPU time & load:** `cpu`, `ps` types — `user`, `system`, `load_*`, …
- **Core PMC / FLOPs / frequency:** `intel_*pmc3`, `amd64_pmc`, `cpu_counter_metrics` — `FP_ARITH_*`, `FLOPS`, `INST_RETIRED`, `APERF`, `MPERF`, …
- **DRAM bandwidth:** `intel_*_imc`, `arm_imc`, `amd64_df` — `CAS_READS` / `CAS_WRITES`, `MBW_CHANNEL_*`
- **GPU:** `nvidia_gpu`, `amd_gpu` — `gpu_util`, `tensor_active`, `power_usage`, …
- **High-speed fabric:** `host_ib`, `opa` — `port_*`, `Port*` counters
- **Ethernet / LNET:** `net`, `lnet` — `rx_bytes`, `tx_bytes`, …
- **Local disk:** `block` — `rd_sectors`, `wr_sectors`, …
- **Shared filesystem:** `llite`, `mdc`, `osc`, `nfs` — bytes, ops, Lustre `mds_*` / `ost_*`
- **Memory & NUMA:** `mem`, NUMA meminfo fields on `mem`, `numa`, `vm`
- **Power:** `intel_rapl`, `amd64_rapl`, `cpu_counter_metrics` (`DCGM_CPU_POWER_*`), GPU power fields
- **Process footprint:** `proc` — `VmRSS`, `VmHWM`, …

---

## Universal vs explicit code references

Every event name below is stored in **`host_data.event`** when the monitor emits it (subject to site `exclude_types` / hardware maps). All such rows flow through the **universal pipeline** in the table above through the ORM model.

The **Additional references** subsection per variable lists repository files that contain a **string literal** with that event name (metrics, plots, tests, metadata). It excludes the generated `variableMetadataMonitorEvents.ts` blob and the generator script’s description table, so you see *behavioral* references only. Files named `test_*.py` under `analysis/metrics/lib/plot/` are unit tests for plotting even when the path does not contain a `tests/` directory; treat them like other test modules when tracing usage.

**PMC note:** `CTL*` / `CTR*` (and some `V*_CTL*` / `V*_CTR*`) names appear in **`!` schema lines** in raw archives; dbload maps them to logical events (for example `INST_RETIRED`) before insert. Those logical names are what appear in `host_data.event` for PMC rows.

---

## Diagnostic guidance (events not wired into analysis)

Many counters are ingested and visible in type-detail / raw `host_data` views but are **not** rolled into default job summary plots or `metrics_data` aggregates. For those, this document adds **Diagnostic guidance**: practical ways operators and performance engineers can use rates (`delta`, `arc`) and cross-metrics checks to explain bottlenecks, faults, or imbalance. Guidance follows common Linux / HPC / fabric practice (kernel docs, vendor counter manuals, and standard wait-state interpretation).

---

## Variable catalog (alphabetical)

### `MBW_CHANNEL_0`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_hover_tooltips.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py

### `MBW_CHANNEL_1`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `MBW_CHANNEL_2`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `MBW_CHANNEL_3`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `MBW_CHANNEL_4`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `MBW_CHANNEL_5`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `MBW_CHANNEL_6`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `MBW_CHANNEL_7`

- **Definition:** AMD memory bandwidth counter for one DF DRAM channel (ingest maps encodings to MBW_CHANNEL_n).
- **Domain:** DRAM / memory controller
- **Typical `host_data.type` values:** `amd64_df`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py

### `READ_ops`

- **Definition:** NFS READ RPC operation count (mountstats).
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** `nfs`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `SSE_DOUBLE_PACKED`

- **Definition:** Intel core PMU: retired SSE/AVX packed double-precision FP operations (legacy FLOP proxy).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `SSE_DOUBLE_SCALAR`

- **Definition:** Intel core PMU: retired SSE/AVX double-precision scalar FP operations (legacy FLOP proxy).
- **Domain:** CPU core performance (PMC)
- **Typical `host_data.type` values:** `intel_4pmc3`, `intel_8pmc3`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `WRITE_ops`

- **Definition:** NFS WRITE RPC operation count.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

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

### `active`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Application / library code:** hpcperfstats/site/lib/machine/management/commands/pg_connection_stats.py

### `allocstall`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `anon_huge_pages`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `anon_pages`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `aperf`

- **Definition:** Active cycles (util-scaled). Grace DCGM fail-soft: act = mperf * (util_total / 100); on x86, MSR/FIXED actual/unhalted cycle counters rename to this key.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/analysis/metrics/tests/test_utils_get_type.py

### `arm_dram_bw_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `arm_est_flops`

- **Definition:** Grace host_cpu_hw: estimated floating-point operations per interval as SP+DP scalar only (does not include arm_int8_ops / arm_int16_ops).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `arm_int16_ops`

- **Definition:** Grace host_cpu_hw: INT16 arithmetic operations from ASE_SVE_INT16_SPEC (PAPI fail-soft may report zero).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py

### `arm_int8_ops`

- **Definition:** Grace host_cpu_hw: INT8 arithmetic operations from ASE_SVE_INT8_SPEC (PAPI fail-soft may report zero).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py

### `bounce`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `branch_inst_retired`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `branch_inst_retired_miss`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `bypass_cha_imc_all`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `bytes_avail`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

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

### `cpu_clock_est_cycles`

- **Definition:** Active cycles (same as aperf under Grace DCGM fail-soft util-scaled estimate). PAPI may overwrite when measured cycles are nonzero.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cpu_peak_dram_bw_bytes_per_s`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py

### `cpu_peak_fp64_flops_per_s`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py

### `cpu_peak_hbm_bw_bytes_per_s`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py

### `cpu_peak_source`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cpu_util_irq_accum_us`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cpu_util_nice_accum_us`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cpu_util_sys_accum_us`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cpu_util_total_accum_us`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cpu_util_user_accum_us`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ctxt`

- **Definition:** Context switches (global, from /proc/stat via ps stats type).
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Load average and context-switch rates contextualize CPU contention: high `ctxt` with moderate CPU counters may indicate excessive threading or I/O wakeups.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cycles_unhalted_core`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `cycles_unhalted_ref`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dcgm_cpu_power_limit_w`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py

### `dcgm_cpu_power_util_w`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/node_power_est.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_node_power_est.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `direct_write`

- **Definition:** NFS direct I/O write bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `dirty`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dispatch_stall_cycles0`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dispatch_stall_cycles1`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_act_count`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_cas_reads`

- **Definition:** Memory-controller DRAM CAS read events (Intel uncore IMC or normalized ARM IMC), used for DRAM bandwidth.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/analysis/metrics/tests/test_utils_get_type.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `dram_cas_writes`

- **Definition:** Memory-controller DRAM CAS write events.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/analysis/metrics/tests/test_utils_get_type.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `dram_chan0_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_chan1_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_chan2_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_chan3_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_energy`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dram_pre_count_miss`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `dtlb_load_misses_miss_causes_a_walk`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `errors`

- **Definition:** LNET error counter.
- **Domain:** Lustre LNET
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** LNET message and drop counters isolate router or NIC issues on Lustre networks; correlate drops with application I/O phases and remote mount health.
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_archive_maint.py

### `excessive_buffer_overrun_errors`

- **Definition:** InfiniBand port counter: excessive buffer overruns (base IB sysfs counters).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `file_pages`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
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

### `fp16_active`

- **Definition:** FP16 pipe activity percent (DCGM PROF on NVIDIA; shared KEY on `amd_gpu`).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `fp32_active`

- **Definition:** FP32 pipe activity percent (DCGM PROF on NVIDIA; shared KEY on `amd_gpu`).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `fp64_active`

- **Definition:** FP64 pipe activity ratio as percent. DCGM PROF on `nvidia_gpu`; same semantic field on `amd_gpu` (wave/SM naming differs).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `fp_arith_inst_retired_128b_packed_double`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_arith_inst_retired_128b_packed_single`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_arith_inst_retired_256b_packed_double`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_arith_inst_retired_256b_packed_single`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_arith_inst_retired_512b_packed_double`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_arith_inst_retired_512b_packed_single`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_arith_inst_retired_scalar_double`

- **Definition:** Grace host_cpu_hw: retired scalar double-precision FP arithmetic instructions.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `fp_arith_inst_retired_scalar_single`

- **Definition:** Grace host_cpu_hw: retired scalar single-precision FP arithmetic instructions.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/site/lib/machine/tests/test_metrics.py

### `fp_comp_ops_exe_sse_fp_packed`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_comp_ops_exe_sse_fp_scalar`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_ops_merge`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fp_ops_retired`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_utils_get_type.py

### `fs_bytes_avail`

- **Definition:** Lustre llite mount capacity: available bytes for unprivileged users from sysfs (gauge).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fs_bytes_free`

- **Definition:** Lustre llite mount capacity: free bytes from sysfs (gauge).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fs_bytes_total`

- **Definition:** Lustre llite mount capacity: total bytes from sysfs (gauge; not a rate).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fs_files_free`

- **Definition:** Lustre llite mount capacity: free inode count from sysfs (gauge).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `fs_files_total`

- **Definition:** Lustre llite mount capacity: total inode count from sysfs (gauge).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_count`

- **Definition:** GPU device count for the monitor row. DCGM-visible GPU count on `nvidia_gpu`; `amd_gpu` schema documents a stub row count (often 1).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gpu_job_detail_summary.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/site/lib/machine/api.py; hpcperfstats/site/lib/machine/cache_utils.py; hpcperfstats/site/lib/machine/job_detail_artifacts.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_api_coverage_gaps.py; hpcperfstats/site/lib/machine/tests/test_job_detail_artifacts_prewarm.py; hpcperfstats/site/lib/machine/tests/test_job_detail_fsio.py; hpcperfstats/site/lib/machine/tests/test_job_detail_gpu.py; hpcperfstats/site/lib/machine/tests/test_job_detail_openapi_wire_contract.py; hpcperfstats/site/lib/machine/tests/test_openapi_wire_contracts.py

### `gpu_dram_active`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_flops`

- **Definition:** Cumulative estimated GPU FLOPs (monitor-integrated model; both GPU types).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/roofline.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py; hpcperfstats/site/lib/machine/job_plot_artifacts.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py

### `gpu_mem_bw_bytes_rate`

- **Definition:** Estimated GPU memory bandwidth (bytes/s) from the monitor model. DCGM-backed on `nvidia_gpu`; `amd_gpu` uses the same schema key.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `gpu_mem_free_mb`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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

### `gpu_mem_total_mb`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `gpu_mem_used_mb`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `gpu_mem_util`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `gpu_mem_write_bytes`

- **Definition:** Cumulative estimated GPU memory write bytes (model; shared schema).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_nvlink_rx_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_nvlink_tx_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_pcie_replay_counter`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_pcie_rx_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_pcie_tx_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_peak_fp64_flops_per_s`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py; hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py

### `gpu_peak_io_link_bw_bytes_per_s`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py; hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py

### `gpu_peak_mem_bw_bytes_per_s`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/roofline_peaks.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_roofline_jid_table.py; hpcperfstats/analysis/metrics/tests/test_roofline_peaks.py

### `gpu_peak_source`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_sm_clock`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_util`

- **Definition:** GPU utilization percent. Populated from DCGM on `nvidia_gpu`; from GPUPerfAPI (or stub zeros) on `amd_gpu`.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** `amd_gpu`, `nvidia_gpu`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gpu_job_detail_summary.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_gpu_job_detail_summary.py; hpcperfstats/analysis/metrics/tests/test_hover_tooltips.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_job_detail_gpu.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `gpu_xe_link_rx_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `gpu_xe_link_tx_bytes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `hbm_cas_reads`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `hbm_cas_writes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `huge_pages_free`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `huge_pages_total`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `idle`

- **Definition:** Cumulative CPU idle time.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Diagnostic guidance:** Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms (`irq`/`softirq`), and low-priority work (`nice`) from useful compute.
- **Application / library code:** hpcperfstats/site/lib/machine/management/commands/pg_connection_stats.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_telemetry_bounds.py; hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage_compose.py

### `in_flight`

- **Definition:** Number of I/O requests currently in flight to the device.
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `inactive`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `inode_use`

- **Definition:** VFS: inodes in use (inode-state).
- **Domain:** VFS
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VFS cache pressure can precede slow path lookups under millions of small files; compare with Lustre metadata rates.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `instr_retired`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/analysis/metrics/tests/test_utils_get_type.py

### `instr_retired_any`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
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
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py

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

### `l1d_replacement`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `l2_lines_in_all`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ldlm_cancel`

- **Definition:** Lustre client metadata (MDC) or OSC statistic from /proc/fs/lustre.
- **Domain:** Lustre client (llite / mdc / osc)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** MDC/OSC/LDLM counters expose client–server metadata and lock behavior; spikes during specific job steps often indicate small-file churn, lock contention, or aggressive stat/cache behavior.
- **Tests:** hpcperfstats/site/lib/machine/tests/test_jid_table.py

### `link_downed`

- **Definition:** InfiniBand port counter: failed link error recoveries (link went down; monitor comment in ib.c).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `link_error_recovery`

- **Definition:** InfiniBand port counter: successful link error recovery events (monitor ib.c).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `llc_lookup_data_read_local`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `llc_lookup_write`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `local_node`

- **Definition:** Local-node memory accesses (numastat).
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** NUMA allocator and access counters guide process placement: rising `numa_miss` or `other_node` with compute-bound jobs suggests binding or first-touch policy tuning.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `ls_dispatch`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mapped`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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

### `mem_free`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py

### `mem_load_uops_retired_l1_hit`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_load_uops_retired_l2_hit`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_load_uops_retired_llc_hit`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_total`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py

### `mem_uncore_retired_local_dram`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_uncore_retired_remote_dram`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_uops_retired_all_loads`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `mem_used`

- **Definition:** System V shared memory: total bytes used across segments.
- **Domain:** SysV shared memory
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `module_power_usage`

- **Definition:** NVIDIA-only (`nvidia_gpu` / DCGM): module-scope power (watts) on integrated packages. Omitted from `amd_gpu` KEYS.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/node_power_est.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_node_power_est.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `mperf`

- **Definition:** Reference cycles (wall/TSC-like). Grace DCGM fail-soft: ref = clock_khz * delta_t_us / 1000; on x86, MSR/FIXED reference cycle counters rename to this key.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

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

### `nfs_unstable`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `nice`

- **Definition:** Cumulative CPU time in low-priority user mode.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/zstd_cli.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `normal_read`

- **Definition:** NFS normal read bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** `nfs`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_jid_table.py

### `normal_write`

- **Definition:** NFS normal write bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_jid_table.py

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

### `numa_foreign`

- **Definition:** Pages counted as foreign to the allocating node.
- **Domain:** NUMA statistics
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `page_tables`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pageoutrun`

- **Definition:** Kernel VM statistic from /proc/vmstat (Linux kernel documentation).
- **Domain:** Kernel VM (/proc/vmstat)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** VM counters highlight reclaim and allocation stalls; rising `allocstall` or kswapd activity with flat RSS can mean memory overcommit or fragmentation. Correlate with OOM events and per-job `Vm*` process stats.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `peak_calc_version`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
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

### `pkg_energy`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/node_power_est.py; hpcperfstats/dbload/lib/monitor_naming/canonical.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_node_power_est.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `port_error_counter_summary`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_mark_fecn`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
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

### `port_rcv_becn`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_bubble`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_rcv_constraint_errors`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `port_rcv_data`

- **Definition:** InfiniBand counter: payload bytes received.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** `host_ib`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `port_rcv_errors`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `port_rcv_fecn`

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py

### `port_rcv_remote_physical_errors`

- **Definition:** InfiniBand inbound physical-layer error counter.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `port_rcv_switch_relay_errors`

- **Definition:** InfiniBand switch relay errors on received traffic.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `port_xmit_data`

- **Definition:** InfiniBand counter: payload bytes transmitted (width/units per IBTA and sysfs docs).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** `host_ib`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `port_xmit_discards`

- **Definition:** Packets not transmitted because the port was down or congested.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py

### `port_xmit_time_cong`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_wait`

- **Definition:** Time waiting for credits or arbitration (vendor-specific units).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_wait_data`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `port_xmit_wasted_bw`

- **Definition:** Network or fabric port performance counter (InfiniBand sysfs, extended MAD, or Omni-Path).
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Use xmit vs rcv data and packet counters to find asymmetric communication patterns; error and discard counters flag link, credit, or congestion problems on the HFI.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `power_usage`

- **Definition:** GPU power draw (watts). DCGM on `nvidia_gpu`; device-reported path on `amd_gpu` when available.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/node_power_est.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_node_power_est.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `pp0_energy`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `pp1_energy`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py

### `rd_ticks`

- **Definition:** Time spent on read I/O (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `read_bytes`

- **Definition:** Cumulative bytes read on the Lustre client from llite or OSC `/proc/fs/lustre/*/stats` (legacy name; canonical emit is `vfs_read_bytes`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** `llite`
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/site/lib/machine/job_detail_artifacts.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_jid_table.py; hpcperfstats/site/lib/machine/tests/test_job_detail_artifacts_prewarm.py; hpcperfstats/site/lib/machine/tests/test_job_detail_fsio.py

### `reqs`

- **Definition:** Lustre OSC: sample count taken from `req_waittime` lines in `/proc/fs/lustre/osc/*/stats` (paired with `wait`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `resource_stalls_any`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `retired_branch_instr`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `retired_instructions`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `retired_misp_branch_instr`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `rx_fifo_errors`

- **Definition:** Linux netdev: receiver FIFO overrun errors.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `rx_frame_errors`

- **Definition:** Linux netdev: framing errors (misaligned or malformed Ethernet frames).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `rx_length_errors`

- **Definition:** Linux netdev: received frames with invalid length field.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `rx_missed_errors`

- **Definition:** Linux netdev: packets missed by the receiver (often ring buffer exhaustion).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `rx_packets`

- **Definition:** Packets received (per-interface sysfs).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `server_write`

- **Definition:** NFS server-side write bytes.
- **Domain:** NFS client (mountstats)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `sf_evictions_mes`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `simd_fp_256_packed_double`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `slab`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `sm_occupancy`

- **Definition:** Occupancy percent: DCGM warp occupancy on NVIDIA; AMD uses active-wave occupancy wording in schema.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `softirq`

- **Definition:** Cumulative CPU time in softirq bottom halves.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Diagnostic guidance:** Break down non-user CPU time to distinguish disk wait (`iowait`), interrupt storms (`irq`/`softirq`), and low-priority work (`nice`) from useful compute.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sw_port_congestion`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sw_rx_bytes`

- **Definition:** IB switch-port received payload bytes (MAD extended 64-bit counters).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sw_rx_packets`

- **Definition:** IB switch-port received packets.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sw_tx_bytes`

- **Definition:** IB switch-port transmitted payload bytes.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `sw_tx_packets`

- **Definition:** IB switch-port transmitted packets.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `symbol_error`

- **Definition:** Minor link symbol errors on InfiniBand.
- **Domain:** InfiniBand / Omni-Path / HFI
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `sysio_power_usage`

- **Definition:** NVIDIA-only (`nvidia_gpu` / DCGM): SysIO instantaneous power (watts). Not present in the slimmer `amd_gpu` monitor schema.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** GPU cumulative or instantaneous model counters support kernel efficiency and memory traffic diagnosis; `clocks_event_reasons` bitmasks flag thermal or power throttling. Compare link-byte counters with HBM bandwidth estimates for PCIe-bound jobs.
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `system`

- **Definition:** Cumulative CPU time in kernel mode.
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_for_metrics.py; hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_job_detail_fsio.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `temperature`

- **Definition:** GPU temperature (degrees C). DCGM on `nvidia_gpu`; `amd_gpu` when GPUPerfAPI supplies it.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Derive time-series rates from `delta` or `arc` in `host_data`, compare across hosts for imbalance, and correlate peaks with application logs or known I/O/communication phases. Type-detail and ad-hoc queries expose this signal even when job-level metrics omit it.
- **Application / library code:** hpcperfstats/dbload/lib/sync_timedb_parsing.py

### `tensor_active`

- **Definition:** GPU tensor/matrix pipe activity as percent in monitor output. On `nvidia_gpu` this comes from DCGM PROF; `amd_gpu` shares the same KEY name with a slimmer schema.
- **Domain:** GPU (NVIDIA DCGM / AMD GPUPerfAPI-style)
- **Typical `host_data.type` values:** `amd_gpu`, `nvidia_gpu`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py; hpcperfstats/dbload/lib/sync_timedb_parsing.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `tensor_dfma_active`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `tensor_hmma_active`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `tensor_imma_active`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

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

### `threads`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `time_in_queue`

- **Definition:** Accumulated time requests spent in the I/O scheduler queue (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `tx_aborted_errors`

- **Definition:** Linux netdev: aborted transmissions (driver or hardware abort).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `tx_bytes`

- **Definition:** Bytes transmitted (Ethernet per-interface sysfs, Lustre LNET, or other typeâuse host_data.type).
- **Domain:** Ethernet (per-interface); LNET may reuse byte keys
- **Typical `host_data.type` values:** `lnet`, `net`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `tx_carrier_errors`

- **Definition:** Linux netdev: loss of carrier during transmit (cable, duplex, or link partner).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `tx_fifo_errors`

- **Definition:** Linux netdev: transmit FIFO errors (underrun/overrun, driver dependent).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `tx_heartbeat_errors`

- **Definition:** Linux netdev: heartbeat / half-duplex loss-of-carrier style errors.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/site/lib/machine/tests/test_metrics.py

### `tx_window_errors`

- **Definition:** Linux netdev: classic transmitter window errors on outbound frames.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/plot/summaryplot.py

### `uid`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `user`

- **Definition:** Cumulative CPU time in user mode (per-core counter; units per Linux /proc/stat, typically jiffies).
- **Domain:** Host CPU time (/proc/stat style)
- **Typical `host_data.type` values:** `cpu`
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_for_metrics.py; hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py; hpcperfstats/analysis/metrics/tests/test_metrics_telemetry_bounds.py; hpcperfstats/analysis/metrics/tests/test_per_interval_rate.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_job_detail_artifacts_prewarm.py; hpcperfstats/site/lib/machine/tests/test_job_detail_fsio.py; hpcperfstats/site/lib/machine/tests/test_metrics.py; hpcperfstats/site/lib/machine/tests/test_update_metrics_diagnosis_compose.py; hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage_compose.py

### `vfs_alloc_inode_ops`

- **Definition:** Lustre llite: inode allocation operations (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_close_ops`

- **Definition:** Lustre llite: `close(2)` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_create_ops`

- **Definition:** Lustre llite: file create operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_direct_read_bytes`

- **Definition:** Lustre llite: cumulative direct-I/O read bytes on this mount.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_direct_write_bytes`

- **Definition:** Lustre llite: cumulative direct-I/O write bytes on this mount.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_dirty_page_hits`

- **Definition:** Lustre llite: dirty client-page cache hits.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_dirty_page_misses`

- **Definition:** Lustre llite: dirty client-page cache misses.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_flock_ops`

- **Definition:** Lustre llite: advisory `flock` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_fsync_ops`

- **Definition:** Lustre llite: `fsync` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_getattr_ops`

- **Definition:** Lustre llite: getattr / stat-style metadata operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_getxattr_ops`

- **Definition:** Lustre llite: `getxattr` syscall count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_inode_permission_ops`

- **Definition:** Lustre llite: inode permission check count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_ioctl_ops`

- **Definition:** Lustre llite: `ioctl` operation count on this mount.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_link_ops`

- **Definition:** Lustre llite: hard `link` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_listxattr_ops`

- **Definition:** Lustre llite: `listxattr` syscall count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_lookup_ops`

- **Definition:** Lustre llite: pathname `lookup` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_mkdir_ops`

- **Definition:** Lustre llite: `mkdir` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_mknod_ops`

- **Definition:** Lustre llite: `mknod` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_mmap_ops`

- **Definition:** Lustre llite: `mmap` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_open_ops`

- **Definition:** Lustre llite: `open(2)` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_osc_read_bytes`

- **Definition:** Lustre llite: bytes attributed to the OSC read path in llite stats.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_osc_write_bytes`

- **Definition:** Lustre llite: bytes attributed to the OSC write path in llite stats.
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_read_bytes`

- **Definition:** Lustre llite: cumulative bytes read on this client mount (canonical vfs_* KEY from `/proc/fs/lustre/llite/*/stats`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `vfs_read_ops`

- **Definition:** Lustre llite: `read(2)` operation count (canonical vfs_* KEY; volume in `vfs_read_bytes`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_readdir_ops`

- **Definition:** Lustre llite: `readdir` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_removexattr_ops`

- **Definition:** Lustre llite: `removexattr` syscall count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_rename_ops`

- **Definition:** Lustre llite: `rename` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_rmdir_ops`

- **Definition:** Lustre llite: `rmdir` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_seek_ops`

- **Definition:** Lustre llite: `seek` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vfs_setattr_ops`

- **Definition:** Lustre llite: `setattr` metadata updates (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_setxattr_ops`

- **Definition:** Lustre llite: `setxattr` syscall count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_statfs_ops`

- **Definition:** Lustre llite: `statfs` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_symlink_ops`

- **Definition:** Lustre llite: `symlink` creation operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_truncate_ops`

- **Definition:** Lustre llite: `truncate` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_unlink_ops`

- **Definition:** Lustre llite: `unlink` operation count (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py

### `vfs_write_bytes`

- **Definition:** Lustre llite: cumulative bytes written on this client mount (canonical vfs_* KEY).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Application / library code:** hpcperfstats/analysis/metrics/lib/gen/jid_table.py; hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/analysis/metrics/lib/llite_metadata_iops_events.py; hpcperfstats/analysis/metrics/lib/metrics.py; hpcperfstats/analysis/metrics/lib/plot/summaryplot.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py

### `vfs_write_ops`

- **Definition:** Lustre llite: `write(2)` operation count (canonical vfs_* KEY; volume in `vfs_write_bytes`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_data`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_exe`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_hwm`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_lck`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_lib`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_peak`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_pte`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_rss`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_size`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_stk`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `vm_swap`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `wait`

- **Definition:** Lustre OSC: cumulative microseconds from the `req_waittime` sum field in `/proc/fs/lustre/osc/*/stats` (use deltas with `reqs` for average wait in a window).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Lustre llite operation counter; unusually high `getxattr`/`inode_permission` rates often accompany metadata-heavy tools or security modules scanning many files.
- **Tests:** hpcperfstats/site/lib/machine/tests/test_api_view_matrix.py; hpcperfstats/site/lib/machine/tests/test_update_metrics.py

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
- **Application / library code:** hpcperfstats/analysis/metrics/lib/metrics.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_metrics_schema_guards.py

### `wr_ticks`

- **Definition:** Time spent on write I/O (milliseconds).
- **Domain:** Block device I/O
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Compare read vs write mix and IOPS to `io_ticks` / queue time to separate throughput limits from latency or scheduler backlog. Sudden merge drops or rising `in_flight` often precede local filesystem or single-device saturation on a node.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

### `write_bytes`

- **Definition:** Cumulative bytes written on the Lustre client from llite or OSC `/proc/fs/lustre/*/stats` (legacy name; canonical emit is `vfs_write_bytes`).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** `llite`
- **Diagnostic guidance:** Lustre client statistic; plot rates over time and compare nodes for skew. Combine with MDS/OSC counters when metadata or lock contention is suspected.
- **Application / library code:** hpcperfstats/analysis/metrics/lib/job_detail_fsio.py; hpcperfstats/site/lib/machine/job_detail_artifacts.py
- **Tests:** hpcperfstats/analysis/metrics/tests/test_job_detail_fsio.py; hpcperfstats/analysis/metrics/tests/test_summaryplot_jid_table.py; hpcperfstats/site/lib/machine/tests/test_jid_table.py; hpcperfstats/site/lib/machine/tests/test_job_detail_artifacts_prewarm.py; hpcperfstats/site/lib/machine/tests/test_job_detail_fsio.py

### `writeback`

- **Definition:** Telemetry field published by the HPCPerfStats monitor as host_data.event (see monitor/src for the owning stats type).
- **Domain:** General / multi-type (see monitor `host_data.type`)
- **Typical `host_data.type` values:** *(infer from job schema / monitor enablement)*
- **Diagnostic guidance:** Raw monitor field: derive rates from `delta`/`arc` in `host_data`, compare hosts and time windows, and correlate with job scheduler steps or known I/O phases.
- **Additional references:** *(none outside universal ingest / schema — may still appear in type-detail API, ad-hoc queries, and Bokeh hovers keyed by raw `event`)*

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

