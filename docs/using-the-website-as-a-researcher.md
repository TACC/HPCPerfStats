# Using HPCPerfStats on the Web — Guide for Researchers and HPC Users


This guide is for users and researchers working on clusters tracked by HPCPerfStats and focuses on using HPCPerfStats website data to understand application runtime performance and diagnostics; it was last updated on 2026-05-07.


This document is ordered so the **most decision-relevant ideas come first**. Deeper catalog-style detail appears in later sections.

> **Data availability note:** Not all data appears for every job or architecture. When data is missing, the interface reports this directly in the relevant panel/tab.

## Index

- [1. Finding jobs and reading the job list](#1-finding-jobs-and-reading-the-job-list)
- [2. What you should do first on a job page](#2-what-you-should-do-first-on-a-job-page)
- [3. Job detail - scheduler and accounting fields](#3-job-detail--scheduler-and-accounting-fields)
- [4. Supporting panels on the job page](#4-supporting-panels-on-the-job-page)
- [5. Job-level metrics catalog](#5-job-level-metrics-catalog)
- [6. Data/plot surfaces and what they mean diagnostically](#6-dataplot-surfaces-and-what-they-mean-diagnostically)
- [7. Failure-mode checklist](#7-failure-mode-checklist)
- [8. Appendix - concept guide with external references](#8-appendix--concept-guide-with-external-references)
- [9. Related references in this repository](#9-related-references-in-this-repository)

---

## 1. Finding jobs and reading the job list

- **Search home**: Browse by **year** or **date** to reach filtered job lists.
- **Expanded search**: Use expanded search when you need to find specific jobs quickly (for example by job ID, host, user, project/account, queue, time window, node count, node-hours, or derived metric thresholds), then open the matching row from the filtered results. The **?** help icon next to each field defines the parameter. Derived metric choices match the **Metrics** tab on each job detail page, and those filters search prepared job-level metric summaries.
- **Job list table**: Typical columns include job ID, submit/start/end times, **runtime**, **requested time (timelimit)**, resource shape (**nodes**, **cores**), **user**, **project/account**, **queue**, **state**, and **job name**. Row **background color** reflects completion state (e.g. completed vs failed vs other).
- **Histograms** (where configured): Distribution thumbnails for metrics such as **runtime**, **node count**, and **queue wait** help you see whether your job is typical for that filter.
- **Performance Data** column: Short status labels (e.g. summary available, monitoring gaps, not summarized yet) indicate current data readiness for each row.

---

## 2. What you should do first on a job page

1. **Read Job overview first** (status, runtime, queue, cores/nodes, user/project, start/end). This frames the run before touching telemetry.
2. **Open the Summary plot tab** in **Job data**. It is the fastest way to see phase changes and node-to-node divergence across CPU, memory, I/O<sup>[11](#ref-11)</sup>, network, and GPU signals.
3. **Open the Roofline tab** for performance-ceiling context: it contains **both** CPU roofline and GPU roofline<sup>[1](#ref-1)</sup> (GPU roofline appears when required counters exist).
4. **Open the Multiprecision Mix tab** to check CPU/GPU precision activity composition.
5. **Open the Metrics tab** for scalar job-level summaries (averages, peaks, imbalance percentages, detail GPU/FSIO rows). Many rows have a **help icon** for definitions.
6. **Use Device data tab** to jump into per-type detail pages (`/job/<jid>/<type>/`) for raw event families and type-level plots/tables.
7. **Use Processes and Execution and hosts tabs** when you need command-line provenance, XALT library context, and host list verification.

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
| **ncores**             | Core count allocated      | Match to how you launched MPI<sup>[9](#ref-9)</sup>/OpenMP<sup>[10](#ref-10)</sup> (miscounts → wrong binding).                                                                          |
| **nnodes**             | Node count                | Multi-node imbalance metrics and network plots only make sense in this context.                                                            |


---

## 4. Supporting panels on the job page


| Panel / tab                     | Content                                                                                           | Diagnostic use                                                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Job overview**                | Compact high-value fields (jid, status, runtime, queue, user/project, cores/nodes, start/end)    | Fast triage before deeper telemetry checks.                                                                                                                    |
| **Full scheduling record**      | Expanded accounting table with all core scheduler columns                                          | Audit exact scheduler/accounting values and formatting without leaving the page.                                                                               |
| **Resources**                   | Shared filesystem totals (`fsio`), log links, GPU summary table                                    | Validate I/O<sup>[11](#ref-11)</sup> totals, jump to external logs, and quickly assess GPU allocation vs activity.                                                                    |
| **Metrics (tab)**               | Full job-level metrics catalog (`metrics_list`) with short labels + help metadata                 | Single-place scalar bottleneck and imbalance summary.                                                                                                           |
| **Summary plot (tab)**          | Host-level timeline plot with CPU, memory/NUMA<sup>[4](#ref-4)</sup>/DRAM, fabric/filesystem, GPU, and node-power traces | Best first visual scan for phase changes, host outliers, and cross-signal coupling (for example GPU drops while fabric spikes).                              |
| **Roofline (tab)**              | CPU roofline and GPU roofline (PCIe/NVLink<sup>[7](#ref-7)</sup>)                                                        | Distinguish compute-limited vs bandwidth/link-limited behavior and prioritize the right optimization work.                                                     |
| **Multiprecision Mix (tab)**    | CPU and GPU precision-activity panels over time                                                    | Verify whether the run is using expected mixed-precision paths<sup>[6](#ref-6)</sup> and detect precision mix drift across runs or code versions.                                         |
| **Processes (tab)**             | Distinct process command lines (`proc_list`)                                                       | Confirm what actually executed (wrappers, launch depth, wrong env, etc.).                                                                                     |
| **Execution and hosts (tab)**   | XALT execution path/cwd/libset and host list                                                       | Environment drift, module/library mismatches, and host-level forensics.                                                                                       |
| **Device data (tab)**           | Device type names and recorded performance events, with links to type-detail pages                  | Discover collected counter families and drill into per-type analysis.                                                                                          |


### 4.1 Device data table (job page)

Lists each `**host_data.type`** name present for the job (e.g. `cpu`, `mem`, `nvidia_gpu`, `ib_ext`, `llite`, PMC types) and the **event/column names** recorded. **Click the type name** to open the **type detail** page.

### 4.2 Type detail page

- **Plot**: Rates aggregated over devices for that type (Bokeh).
- **Table** (“Counts aggregated…”): Time-bucketed means across hosts/devices for each column—useful when you want **numeric export-style** inspection without hovering the plot.

### 4.3 Host plot

**Host-centric** view for a **time window you choose** (defaults to roughly the last day if you do not narrow it). Use it for **debugging a specific node** outside a single job context (noisy neighbor, hardware issue, or post-mortem on a login or service node if monitored).

---

## 5. Job-level metrics catalog

This section lists the metrics shown in the Job detail **Metrics** tab and how to interpret them.

| Metric key | Short label | What it summarizes | Diagnostic / performance interpretation |
| ---------- | ----------- | ------------------ | --------------------------------------- |
| `avg_blockbw` | Block GB/s | Mean local block-device throughput | High values indicate local scratch/checkpoint pressure; unexpected nonzero can reveal spill to local disk. |
| `avg_cpuusage` | CPU cores | Mean CPU cores used (from user/system/nice) | Low vs allocated cores suggests under-subscription, waiting, serialization, or I/O/network stalls. |
| `avg_sharedfs_iops` | FS IOPS | Mean shared filesystem metadata/op rate | High with low MB/s points to small-file metadata bottlenecks. |
| `avg_sharedfs_bw` | FS MB/s | Mean shared filesystem bandwidth | Sustained high values indicate file I/O-heavy phases; correlate with runtime spikes/checkpoint windows. |
| `avg_ibbw` | IB MB/s | Mean InfiniBand/fabric byte throughput | High values with modest FLOP rate imply communication-heavy behavior<sup>[15](#ref-15)</sup>. |
| `avg_fabric_mb_per_gflops` | MB/GFLOP | Fabric MB per GFLOP | Communication intensity relative to compute; rising with scale often means weaker scaling efficiency. |
| `avg_tensor_active` | Tensor % | Mean tensor pipeline activity | Low on expected tensor workloads suggests kernels not reaching tensor paths. |
| `avg_gpu_mem_bw_gbps` | GPU HBM | Mean GPU memory-bandwidth rate | High with moderate utilization can indicate memory-bound kernels. |
| `avg_fabric_mb_per_avg_tensor` | MB/tensor | Fabric MB per average tensor activity | Communication intensity normalized by tensor activity for GPU+MPI workloads. |
| `avg_flops` | GFLOP/s | Mean achieved FLOP rate | Baseline compute throughput for CPU-side arithmetic. |
| `avg_mbw` | DRAM GB/s | Mean DRAM bandwidth | High with low FLOPs suggests memory-bound CPU phases. |
| `avg_freq` | CPU GHz | Mean CPU frequency | Drops may indicate power/thermal policy or throttling. |
| `avg_ethbw` | Eth MB/s | Mean Ethernet bandwidth | Useful for TCP/object-store workflows that bypass IB paths<sup>[16](#ref-16)</sup>. |
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
| `max_numa_remote_rate` | NUMA rem | Peak NUMA remote-access rate | High values indicate locality/memory placement issues<sup>[4](#ref-4)</sup>. |
| `max_gpu_power` | GPU W max | Peak GPU power draw | Detects power-cap proximity or thermal stress windows. |
| `max_node_power_est_w` | Node W max | Peak estimated node power | Useful for peak power envelope checks and cooling stress. |
| `avg_node_power_est_w` | Node W avg | Mean estimated node power | Energy-to-solution comparisons across runs/configurations. |
| `max_gpu_link_gbps` | GPU link | Peak GPU link bandwidth (PCIe/NVLink aggregate path) | Host-device/device-device transfer pressure indicator. |
| `max_gpu_clock_event_reasons` | GPU clk | Maximum clock event reason bitmask | Nonzero values suggest throttle/clock constraints; correlate with power/temp traces. |
| `mem_hwm` | RSS HWM | High-water memory estimate (MemUsed-Slab-FilePages) | Compare with node RAM for host OOM risk<sup>[14](#ref-14)</sup>. |
| `node_imbalance` | CPU imbal | Node-level CPU rate imbalance | High values indicate decomposition/rank imbalance. |
| `time_imbalance` | Time imbal | Temporal CPU imbalance across job timeline | Flags long underutilized windows or phase imbalance over time. |
| `flops_node_imbalance` | FLOP imbal | Node-level FLOP rate imbalance | Compute work unevenly distributed across nodes. |
| `fabric_node_imbalance` | Fab imbal | Node-level fabric traffic imbalance | Some ranks/nodes communicate disproportionately. |
| `dram_bw_node_imbalance` | DRAM imbal | Node-level DRAM bandwidth imbalance | Memory pressure concentrated on subset of nodes. |
| `lnet_node_imbalance` | LNET imbal | Node-level LNet imbalance | Uneven filesystem/network load distribution. |
| `gpu_util_node_imbalance` | GPU imbal | Node-level GPU utilization imbalance | Multi-node training/inference skew across nodes. |
| `tensor_node_imbalance` | Tensor imbal | Node-level tensor-activity imbalance | Tensor kernels unevenly distributed across participating nodes. |
| `vecpercent_64b` | Vec% DP | Percent of double-precision FLOPs done via vector widths > scalar | Low values on DP-heavy code suggest SIMD/vectorization opportunity<sup>[5](#ref-5)</sup>. |
| `avg_vector_width_64b` | VW DP | Average effective DP vector width | Closer to scalar indicates weak SIMD utilization in DP paths. |
| `vecpercent_32b` | Vec% SP | Percent of single-precision FLOPs done via vector widths > scalar | Low values on SP-heavy code suggest vectorization opportunity<sup>[5](#ref-5)</sup>. |
| `avg_vector_width_32b` | VW SP | Average effective SP vector width | Low average width indicates scalar/short-vector dominated SP execution. |

---

## 6. Data/plot surfaces and what they mean diagnostically

This section covers job-detail surfaces beyond scalar metrics.

### 6.1 Summary plot

- Diagnostic use: fastest phase/host outlier scan across CPU, memory, network, I/O, and GPU traces.
- Performance recommendation: always pair with Metrics tab; peaks in summary often explain extreme scalar maxima and telemetry behavior<sup>[12](#ref-12)</sup>.

### 6.2 Roofline tab (CPU + GPU)

- Diagnostic use: distinguish compute-ceiling vs bandwidth/link-ceiling regimes in the roofline model<sup>[1](#ref-1)</sup>.
- Recommendation: use `avg_flops`<sup>[3](#ref-3)</sup>, `avg_mbw`, `max_gpu_link_gbps`, and fabric ratios to validate roofline reading.
- CPU vs GPU regime quick read:
  - **CPU roofline**: if points sit near the sloped bandwidth line, the phase is memory-bandwidth limited (data movement dominates); if points approach the flat top line, compute throughput dominates.
  - **GPU roofline**: apply the same logic, but include device-memory and interconnect effects; low arithmetic intensity<sup>[2](#ref-2)</sup> phases are often HBM or link limited, while high-intensity phases can become tensor/core compute limited.
  - **Interconnect context**: GPU scaling limits can reflect PCIe or NVLink behavior<sup>[7](#ref-7)</sup>, not just kernel math throughput.
  - **What to do with this**: memory/link-limited phases usually benefit from locality, data-layout, batching, or communication changes; compute-limited phases usually benefit from kernel efficiency, vector/tensor usage, and occupancy improvements.

### 6.3 Multiprecision Mix tab (CPU and GPU)

- Diagnostic use: quantify mixed-precision path composition (DP/SP/tensor) by timeline<sup>[6](#ref-6)</sup>.
- Recommendation: when model/code changes precision policy, compare this tab first, then check throughput/utilization deltas.

### 6.4 Resources panel (FSIO + GPU summary + logs)

- Diagnostic use: rapid verification of I/O volume and GPU occupancy before deep plotting.
- Recommendation: if FS totals are high, check `FS MB/s`, `FS IOPS`, `MDS peak`, and LNET metrics for bottleneck type; this is usually an I/O bottleneck triage path<sup>[11](#ref-11)</sup>.

### 6.5 Execution and hosts tab

- Diagnostic use: detect environment drift (wrong module/library/container path) and host-specific anomalies.
- Recommendation: for regressions with “same script,” verify this tab before tuning code.

### 6.6 Device data tab

- Diagnostic use: confirms which counter families/events were actually collected for this job.
- Recommendation: use this tab to confirm expected telemetry families when interpreting plots/metrics.

---

## 7. Failure-mode checklist

### 7.1 Job did not run intended workload

- Signals: short runtime, flat/empty summary traces, empty process list.
- Check: `Execution and hosts` (exec path/cwd/libset), `Processes`, scheduler record.

### 7.2 Timeout/preemption risk

- Signals: runtime near requested time, abrupt trace cutoff.
- Check: tail-end spikes in FS/fabric/GPU activity; adjust timelimit or checkpoint cadence<sup>[13](#ref-13)</sup>.

### 7.3 Host memory pressure / OOM risk

- Signals: high `mem_hwm`, elevated `NUMA rem`, imbalance in `DRAM imbal`.
- Check: first-touch policy, rank/thread placement, per-rank memory growth.

### 7.4 GPU memory or accelerator under-use

- Signals: low `GPU %`, low `Tensor %`, low `GPU mean %` with full `GPU count`.
- Check: input staging bottlenecks, wrong device mapping, too-small kernels, launch configuration.

### 7.5 Communication-dominated scaling

- Signals: high `IB MB/s`, `Fab peak`, `MB/GFLOP`, `MB/tensor`, packet-rate peaks<sup>[15](#ref-15)</sup>.
- Check: decomposition, collective strategy, message size (`Pkt size`), rank mapping.

### 7.6 Filesystem/metadata bottlenecks

- Signals: high `FS IOPS`, `MDS peak`, `LNET peak`, large FS totals.
- Check: file-per-rank patterns, small sync writes, metadata-heavy loops.

### 7.7 Throttling/power constraints

- Signals: nonzero `GPU clk`, high `GPU W max`, capped `Node W max` with performance dips.
- Check: power caps, thermal conditions, cluster policy limits.

### 7.8 Cross-node imbalance / stragglers

- Signals: elevated `CPU imbal`, `FLOP imbal`, `GPU imbal`, `Tensor imbal`, `Fab imbal`, `LNET imbal`, `DRAM imbal`.
- Check: domain decomposition, skewed rank placement, node health differences.

### 7.9 Data continuity checks

- Signals: gaps in expected plot/metric continuity.
- Check: `Device data` event coverage for expected signal families and compare with similar jobs.

---

## 8. Appendix — concept guide with external references

Use these numbered references when you want background on terms used throughout sections 1-7.

| Ref | Concept | Why it matters for this guide | Reference |
| --- | ------- | ----------------------------- | --------- |
| <span id="ref-1">[1]</span> | Roofline model | Explains the core “bandwidth-limited vs compute-limited” interpretation used in the Roofline tab. | [Roofline model (Wikipedia)](https://en.wikipedia.org/wiki/Roofline_model) |
| <span id="ref-2">[2]</span> | Arithmetic intensity | Defines how much math is done per byte moved; key quantity that places points on a roofline plot. | [Roofline model - Arithmetic Intensity section (Wikipedia)](https://en.wikipedia.org/wiki/Roofline_model#Arithmetic_intensity) |
| <span id="ref-3">[3]</span> | FLOP/s | Basis for throughput metrics and the vertical axis intuition in CPU/GPU roofline analysis. | [Floating-point operations per second (Wikipedia)](https://en.wikipedia.org/wiki/Floating_point_operations_per_second) |
| <span id="ref-4">[4]</span> | NUMA | Helps interpret locality penalties and memory-placement effects behind DRAM/imbalance signals. | [Non-uniform memory access (Wikipedia)](https://en.wikipedia.org/wiki/Non-uniform_memory_access) |
| <span id="ref-5">[5]</span> | SIMD / vectorization | Provides background for vector-width metrics and why scalar-heavy execution can reduce CPU throughput. | [Single instruction, multiple data (Wikipedia)](https://en.wikipedia.org/wiki/Single_instruction,_multiple_data) |
| <span id="ref-6">[6]</span> | Mixed precision | Context for the Multiprecision Mix tab (DP/SP/tensor behavior over time). | [Mixed-precision arithmetic (Wikipedia)](https://en.wikipedia.org/wiki/Mixed-precision_arithmetic) |
| <span id="ref-7">[7]</span> | PCIe vs NVLink | Clarifies GPU interconnect effects behind GPU-link and communication ceilings. | [PCI Express (Wikipedia)](https://en.wikipedia.org/wiki/PCI_Express); [NVLink (Wikipedia)](https://en.wikipedia.org/wiki/NVLink) |
| <span id="ref-8">[8]</span> | Practical roofline tuning workflow | Applied HPC interpretation examples that complement the theory above. | [NERSC Roofline documentation](https://docs.nersc.gov/tools/performance/roofline/) |
| <span id="ref-9">[9]</span> | MPI | Explains distributed-memory message passing terminology used in scheduler and runtime interpretation. | [Message Passing Interface (Wikipedia)](https://en.wikipedia.org/wiki/Message_Passing_Interface) |
| <span id="ref-10">[10]</span> | OpenMP | Background for shared-memory threading terminology used when reasoning about core utilization. | [OpenMP (Wikipedia)](https://en.wikipedia.org/wiki/OpenMP) |
| <span id="ref-11">[11]</span> | I/O | General background for filesystem throughput/IOPS interpretation and bottleneck diagnosis. | [Input/output (Wikipedia)](https://en.wikipedia.org/wiki/Input/output) |
| <span id="ref-12">[12]</span> | Telemetry | Explains instrumentation/measurement framing for interpreting counters and traces. | [Telemetry (Wikipedia)](https://en.wikipedia.org/wiki/Telemetry) |
| <span id="ref-13">[13]</span> | Checkpointing | Background for checkpoint cadence tradeoffs in timeout-risk mitigation. | [Application checkpointing (Wikipedia)](https://en.wikipedia.org/wiki/Application_checkpointing) |
| <span id="ref-14">[14]</span> | Out-of-memory conditions | Context for OOM-risk interpretation in memory-pressure diagnostics. | [Out of memory (Wikipedia)](https://en.wikipedia.org/wiki/Out_of_memory) |
| <span id="ref-15">[15]</span> | InfiniBand | Background for IB/fabric metrics and communication-heavy scaling interpretation. | [InfiniBand (Wikipedia)](https://en.wikipedia.org/wiki/InfiniBand) |
| <span id="ref-16">[16]</span> | Ethernet | Background for Ethernet bandwidth interpretation alongside IB paths. | [Ethernet (Wikipedia)](https://en.wikipedia.org/wiki/Ethernet) |

---

## 9. Related references in this repository

- **Counter and variable catalog:** `docs/MONITOR_VARIABLES.md`

---

## Document history


| Date       | Change                                                                                  |
| ---------- | --------------------------------------------------------------------------------------- |
| 2026-04-03 | Initial researcher-facing guide aligned with current job detail UI and metrics catalog. |
| 2026-05-06 | Reorganized the guide for usability (index-first navigation and clearer section flow), aligned job-detail surfaces/metrics with current UI labels, and added paper-style concept citations with an appendix reference catalog for non-CS/HPC readers. |
| 2026-05-07 | Clarified expanded search help icons and that derived metric filters match the Job detail Metrics tab. |


