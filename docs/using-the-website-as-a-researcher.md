# Using HPCPerfStats on the Web — Guide for Researchers and HPC Users


| Field            | Value                                                                                                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Audience**     | Users and Researchers that use clusters that are tracked by HPCPerfStats                                                                   |
| **Scope**        | Using the data available in HPCPerfStats website with an eye towards understanding application runtime performance and diagnostic utiltiy. |
| **Last updated** | 2026-05-06                                                                                                                                 |


This document is ordered so the **most decision-relevant ideas come first**. Deeper catalog-style detail appears in later sections.

> **Data availability note:** Not all data appears for every job or architecture. When data is missing, the interface reports this directly in the relevant panel/tab.

---

## 1. What you should do first on a job page

1. **Read Job overview first** (status, runtime, queue, cores/nodes, user/project, start/end). This frames the run before touching telemetry.
2. **Open the Summary plot tab** in **Job data**. It is the fastest way to see phase changes and node-to-node divergence across CPU, memory, I/O, network, and GPU signals.
3. **Open the Roofline tab** for performance-ceiling context: it contains **both** CPU roofline and GPU roofline (GPU roofline appears when required counters exist).
4. **Open the Multiprecision Mix tab** to check CPU/GPU precision activity composition.
5. **Open the Metrics tab** for scalar job-level summaries (averages, peaks, imbalance percentages, detail GPU/FSIO rows). Many rows have a **help icon** for definitions.
6. **Use Device data tab** to jump into per-type detail pages (`/job/<jid>/<type>/`) for raw event families and type-level plots/tables.
7. **Use Processes and Execution and hosts tabs** when you need command-line provenance, XALT library context, and host list verification.

---

## 2. Finding jobs and reading the job list

- **Search home**: Browse by **year** or **date** to reach filtered job lists.
- **Job list table**: Typical columns include job ID, submit/start/end times, **runtime**, **requested time (timelimit)**, resource shape (**nodes**, **cores**), **user**, **project/account**, **queue**, **state**, and **job name**. Row **background color** reflects completion state (e.g. completed vs failed vs other).
- **Histograms** (where configured): Distribution thumbnails for metrics such as **runtime**, **node count**, and **queue wait** help you see whether your job is typical for that filter.
- **Performance Data** column: Short status labels (e.g. summary available, monitoring gaps, not summarized yet) indicate current data readiness for each row.

---

## 3. Job detail — scheduler and accounting fields

These fields come from batch accounting (e.g. Slurm) and define the **official** story of the run.


| Field                  | What it is                | Diagnostic use                                                                                                                             |
| ---------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Job ID**             | Unique scheduler id       | Cross-reference logs, support tickets, and reproducer scripts.                                                                             |
| **User**               | Submitter                 | Ownership and fair-share context.                                                                                                          |
| **Project / account**  | Charge or allocation      | Billing and policy (which allocation was charged).                                                                                         |
| **Start / end time**   | Scheduler window          | Align with external logs; detect unexpected early end.                                                                                     |
| **Runtime (s)**        | End − start (as recorded) | Compare to **requested time**: jobs that always use ≪ requested time may be over-requesting; jobs hitting the limit may be **timing out**. |
| **Requested time (s)** | Time limit requested      | Risk of **preemption / timeout** if runtime approaches this.                                                                               |
| **Queue**              | Partition / class         | Explains hardware policy (GPU vs CPU, debug vs production).                                                                                |
| **Job name**           | User-provided label       | Often encodes experiment id or binary name—quick sanity check.                                                                             |
| **Status / state**     | Completion outcome        | **Failed** jobs may still have partial telemetry—still worth opening.                                                                      |
| **ncores**             | Core count allocated      | Match to how you launched MPI/OpenMP (miscounts → wrong binding).                                                                          |
| **nnodes**             | Node count                | Multi-node imbalance metrics and network plots only make sense in this context.                                                            |


---

## 4. Job detail tabs and plots (Bokeh)

The **Job data** section is tabbed.

### 4.1 Summary plot tab

The summary tab shows one Bokeh summary figure over job time, typically with one line per host where data exists.

Common signal families include:

- **CPU usage / instruction activity**
- **Memory / NUMA / DRAM bandwidth**
- **Fabric/network and filesystem throughput**
- **GPU utilization, power, link, memory bandwidth**
- **Estimated node power**

Use it to spot phase boundaries, node outliers, and cross-signal coupling (for example, GPU utilization drops while fabric traffic spikes).

### 4.2 Roofline tab

This tab renders:

- **CPU Roofline**
- **GPU Roofline (PCIe/NVLink)**

CPU roofline reads arithmetic intensity vs achieved FLOP/s against inferred compute/bandwidth ceilings.  
GPU roofline uses GPU-side FLOP and link-byte telemetry.

### 4.3 Multiprecision Mix tab

This tab shows:

- **CPU Multiprecision Mix**
- **GPU Multiprecision Mix**

Each panel maps precision activity across the job timeline.

---

## 5. Device data, type detail, and host plots

### 5.1 Device data table (job page)

Lists each `**host_data.type`** name present for the job (e.g. `cpu`, `mem`, `nvidia_gpu`, `ib_ext`, `llite`, PMC types) and the **event/column names** recorded. **Click the type name** to open the **type detail** page.

### 5.2 Type detail page

- **Plot**: Rates aggregated over devices for that type (Bokeh).
- **Table** (“Counts aggregated…”): Time-bucketed means across hosts/devices for each column—useful when you want **numeric export-style** inspection without hovering the plot.

### 5.3 Host plot

**Host-centric** view for a **time window you choose** (defaults to roughly the last day if you do not narrow it). Use it for **debugging a specific node** outside a single job context (noisy neighbor, hardware issue, or post-mortem on a login or service node if monitored).

---

## 6. Supporting panels on the job page


| Panel / tab                     | Content                                                                                           | Diagnostic use                                                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Job overview**                | Compact high-value fields (jid, status, runtime, queue, user/project, cores/nodes, start/end)    | Fast triage before deeper telemetry checks.                                                                                                                    |
| **Full scheduling record**      | Expanded accounting table with all core scheduler columns                                          | Audit exact scheduler/accounting values and formatting without leaving the page.                                                                               |
| **Resources**                   | Shared filesystem totals (`fsio`), log links, GPU summary table                                    | Validate I/O totals, jump to external logs, and quickly assess GPU allocation vs activity.                                                                    |
| **Metrics (tab)**               | Full job-level metrics catalog (`metrics_list`) with short labels + help metadata                 | Single-place scalar bottleneck and imbalance summary.                                                                                                           |
| **Summary plot (tab)**          | Main host-level summary Bokeh plot                                                                 | Time/phase and host-outlier analysis.                                                                                                                          |
| **Roofline (tab)**              | CPU roofline + GPU roofline panels                                                                 | Ceiling-model interpretation for compute vs bandwidth/link limits.                                                                                             |
| **Multiprecision Mix (tab)**    | CPU and GPU multiprecision mix panels                                                              | Understand precision usage patterns by compute surface.                                                                                                        |
| **Processes (tab)**             | Distinct process command lines (`proc_list`)                                                       | Confirm what actually executed (wrappers, launch depth, wrong env, etc.).                                                                                     |
| **Execution and hosts (tab)**   | XALT execution path/cwd/libset and host list                                                       | Environment drift, module/library mismatches, and host-level forensics.                                                                                       |
| **Device data (tab)**           | Device type names and recorded performance events, with links to type-detail pages                  | Discover collected counter families and drill into per-type analysis.                                                                                          |


---

## 7. Job-level metrics catalog (one-for-one with current labels)

This section mirrors the current Job detail **Metrics** tab exactly: metric name, short label, and how to interpret it.

| Metric key | Short label | What it summarizes | Diagnostic / performance interpretation |
| ---------- | ----------- | ------------------ | --------------------------------------- |
| `avg_blockbw` | Block GB/s | Mean local block-device throughput | High values indicate local scratch/checkpoint pressure; unexpected nonzero can reveal spill to local disk. |
| `avg_cpuusage` | CPU cores | Mean CPU cores used (from user/system/nice) | Low vs allocated cores suggests under-subscription, waiting, serialization, or I/O/network stalls. |
| `avg_sharedfs_iops` | FS IOPS | Mean shared filesystem metadata/op rate | High with low MB/s points to small-file metadata bottlenecks. |
| `avg_sharedfs_bw` | FS MB/s | Mean shared filesystem bandwidth | Sustained high values indicate file I/O-heavy phases; correlate with runtime spikes/checkpoint windows. |
| `avg_ibbw` | IB MB/s | Mean InfiniBand/fabric byte throughput | High values with modest FLOP rate imply communication-heavy behavior. |
| `avg_fabric_mb_per_gflops` | MB/GFLOP | Fabric MB per GFLOP | Communication intensity relative to compute; rising with scale often means weaker scaling efficiency. |
| `avg_tensor_active` | Tensor % | Mean tensor pipeline activity | Low on expected tensor workloads suggests kernels not reaching tensor paths. |
| `avg_gpu_mem_bw_gbps` | GPU HBM | Mean GPU memory-bandwidth rate | High with moderate utilization can indicate memory-bound kernels. |
| `avg_fabric_mb_per_avg_tensor` | MB/tensor | Fabric MB per average tensor activity | Communication intensity normalized by tensor activity for GPU+MPI workloads. |
| `avg_flops` | GFLOP/s | Mean achieved FLOP rate | Baseline compute throughput for CPU-side arithmetic. |
| `avg_mbw` | DRAM GB/s | Mean DRAM bandwidth | High with low FLOPs suggests memory-bound CPU phases. |
| `avg_freq` | CPU GHz | Mean CPU frequency | Drops may indicate power/thermal policy or throttling. |
| `avg_ethbw` | Eth MB/s | Mean Ethernet bandwidth | Useful for TCP/object-store workflows that bypass IB paths. |
| `detail_gpu_active` | GPU active | Number of active GPUs | Lower than allocated GPUs usually means mapping/launcher inefficiency. |
| `detail_gpu_util_max` | GPU max % | Max GPU utilization observed | Peak headroom check; high max with low mean often indicates bursty kernels. |
| `detail_gpu_util_mean` | GPU mean % | Mean GPU utilization observed | Primary “are GPUs doing work?” scalar for the job. |
| `detail_gpu_count` | GPU count | Total GPUs allocated | Sanity check against scheduler request and host topology. |
| `detail_fsio_llite_read_mb` | FSIO llite read | Total Lustre llite read MB | Aggregate client-side read volume for Lustre path. |
| `detail_fsio_llite_write_mb` | FSIO llite write | Total Lustre llite write MB | Aggregate client-side write volume for Lustre path. |
| `detail_fsio_nfs_read_mb` | FSIO NFS read | Total NFS read MB | Aggregate client-side read volume for NFS-backed paths. |
| `detail_fsio_nfs_write_mb` | FSIO NFS write | Total NFS write MB | Aggregate client-side write volume for NFS-backed paths. |
| `avg_gpuutil` | GPU % | Mean GPU utilization (vendor-aware source priority) | Core accelerator utilization KPI; low values indicate feed/scheduling inefficiency. |
| `avg_packetsize` | Pkt size | Mean network packet size | Small average packet sizes imply metadata/collective chatter overhead. |
| `max_fabricbw` | Fab peak | Peak fabric bandwidth | Captures communication bursts that may not appear in averages. |
| `max_lnetbw` | LNET peak | Peak Lustre LNet bandwidth | Peak parallel file-system network pressure. |
| `max_mds` | MDS peak | Peak metadata operation rate | High peaks indicate metadata storms (create/unlink/readdir heavy phases). |
| `max_packetrate` | Pkt/s peak | Peak packet rate | High with small packet size suggests message-rate overhead. |
| `max_opa_congestion_rate` | OPA cong | Peak OPA congestion-related counter rate | OPA-specific network contention indicator. |
| `max_numa_remote_rate` | NUMA rem | Peak NUMA remote-access rate | High values indicate locality/memory placement issues. |
| `max_gpu_power` | GPU W max | Peak GPU power draw | Detects power-cap proximity or thermal stress windows. |
| `max_node_power_est_w` | Node W max | Peak estimated node power | Useful for peak power envelope checks and cooling stress. |
| `avg_node_power_est_w` | Node W avg | Mean estimated node power | Energy-to-solution comparisons across runs/configurations. |
| `max_gpu_link_gbps` | GPU link | Peak GPU link bandwidth (PCIe/NVLink aggregate path) | Host-device/device-device transfer pressure indicator. |
| `max_gpu_clock_event_reasons` | GPU clk | Maximum clock event reason bitmask | Nonzero values suggest throttle/clock constraints; correlate with power/temp traces. |
| `mem_hwm` | RSS HWM | High-water memory estimate (MemUsed-Slab-FilePages) | Compare with node RAM for host OOM risk. |
| `node_imbalance` | CPU imbal | Node-level CPU rate imbalance | High values indicate decomposition/rank imbalance. |
| `time_imbalance` | Time imbal | Temporal CPU imbalance across job timeline | Flags long underutilized windows or phase imbalance over time. |
| `flops_node_imbalance` | FLOP imbal | Node-level FLOP rate imbalance | Compute work unevenly distributed across nodes. |
| `fabric_node_imbalance` | Fab imbal | Node-level fabric traffic imbalance | Some ranks/nodes communicate disproportionately. |
| `dram_bw_node_imbalance` | DRAM imbal | Node-level DRAM bandwidth imbalance | Memory pressure concentrated on subset of nodes. |
| `lnet_node_imbalance` | LNET imbal | Node-level LNet imbalance | Uneven filesystem/network load distribution. |
| `gpu_util_node_imbalance` | GPU imbal | Node-level GPU utilization imbalance | Multi-node training/inference skew across nodes. |
| `tensor_node_imbalance` | Tensor imbal | Node-level tensor-activity imbalance | Tensor kernels unevenly distributed across participating nodes. |
| `vecpercent_64b` | Vec% DP | Percent of double-precision FLOPs done via vector widths > scalar | Low values on DP-heavy code suggest SIMD/vectorization opportunity. |
| `avg_vector_width_64b` | VW DP | Average effective DP vector width | Closer to scalar indicates weak SIMD utilization in DP paths. |
| `vecpercent_32b` | Vec% SP | Percent of single-precision FLOPs done via vector widths > scalar | Low values on SP-heavy code suggest vectorization opportunity. |
| `avg_vector_width_32b` | VW SP | Average effective SP vector width | Low average width indicates scalar/short-vector dominated SP execution. |

---

## 8. Data/plot surfaces and what they mean diagnostically

This section covers job-detail surfaces beyond scalar metrics.

### 8.1 Summary plot

- Diagnostic use: fastest phase/host outlier scan across CPU, memory, network, I/O, and GPU traces.
- Performance recommendation: always pair with Metrics tab; peaks in summary often explain extreme scalar maxima.

### 8.2 Roofline tab (CPU + GPU)

- Diagnostic use: distinguish compute-ceiling vs bandwidth/link-ceiling regimes.
- Recommendation: use `avg_flops`, `avg_mbw`, `max_gpu_link_gbps`, and fabric ratios to validate roofline reading.

### 8.3 Multiprecision Mix tab (CPU and GPU)

- Diagnostic use: quantify precision-path composition (DP/SP/tensor) by timeline.
- Recommendation: when model/code changes precision policy, compare this tab first, then check throughput/utilization deltas.

### 8.4 Resources panel (FSIO + GPU summary + logs)

- Diagnostic use: rapid verification of I/O volume and GPU occupancy before deep plotting.
- Recommendation: if FS totals are high, check `FS MB/s`, `FS IOPS`, `MDS peak`, and LNET metrics for bottleneck type.

### 8.5 Execution and hosts tab

- Diagnostic use: detect environment drift (wrong module/library/container path) and host-specific anomalies.
- Recommendation: for regressions with “same script,” verify this tab before tuning code.

### 8.6 Device data tab

- Diagnostic use: confirms which counter families/events were actually collected for this job.
- Recommendation: use this tab to confirm expected telemetry families when interpreting plots/metrics.

---

## 9. Failure-mode checklist (corrected to current surfaces)

### 9.1 Job did not run intended workload

- Signals: short runtime, flat/empty summary traces, empty process list.
- Check: `Execution and hosts` (exec path/cwd/libset), `Processes`, scheduler record.

### 9.2 Timeout/preemption risk

- Signals: runtime near requested time, abrupt trace cutoff.
- Check: tail-end spikes in FS/fabric/GPU activity; adjust timelimit or checkpoint cadence.

### 9.3 Host memory pressure / OOM risk

- Signals: high `mem_hwm`, elevated `NUMA rem`, imbalance in `DRAM imbal`.
- Check: first-touch policy, rank/thread placement, per-rank memory growth.

### 9.4 GPU memory or accelerator under-use

- Signals: low `GPU %`, low `Tensor %`, low `GPU mean %` with full `GPU count`.
- Check: input staging bottlenecks, wrong device mapping, too-small kernels, launch configuration.

### 9.5 Communication-dominated scaling

- Signals: high `IB MB/s`, `Fab peak`, `MB/GFLOP`, `MB/tensor`, packet-rate peaks.
- Check: decomposition, collective strategy, message size (`Pkt size`), rank mapping.

### 9.6 Filesystem/metadata bottlenecks

- Signals: high `FS IOPS`, `MDS peak`, `LNET peak`, large FS totals.
- Check: file-per-rank patterns, small sync writes, metadata-heavy loops.

### 9.7 Throttling/power constraints

- Signals: nonzero `GPU clk`, high `GPU W max`, capped `Node W max` with performance dips.
- Check: power caps, thermal conditions, cluster policy limits.

### 9.8 Cross-node imbalance / stragglers

- Signals: elevated `CPU imbal`, `FLOP imbal`, `GPU imbal`, `Tensor imbal`, `Fab imbal`, `LNET imbal`, `DRAM imbal`.
- Check: domain decomposition, skewed rank placement, node health differences.

### 9.9 Data continuity checks

- Signals: gaps in expected plot/metric continuity.
- Check: `Device data` event coverage for expected signal families and compare with similar jobs.

---

## 10. Related references in this repository

- **Counter and variable catalog:** `docs/MONITOR_VARIABLES.md`

---

## Document history


| Date       | Change                                                                                  |
| ---------- | --------------------------------------------------------------------------------------- |
| 2026-04-03 | Initial researcher-facing guide aligned with current job detail UI and metrics catalog. |
| 2026-05-06 | Updated job-detail guidance for tabbed UI (Summary/Roofline/Multiprecision/Metrics/Execution/Device data). |
| 2026-05-06 | Consolidated data-availability messaging into one global note and removed repeated per-surface availability caveats. |
| 2026-05-06 | Removed backend implementation details and staff-only references; kept guidance user-facing only. |


