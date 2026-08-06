# Using HPCPerfStats on the Web — Guide for Researchers and HPC Users


This guide is for users and researchers working on clusters tracked by HPCPerfStats and focuses on using HPCPerfStats website data to understand application runtime performance and diagnostics; it was last updated on 2026-08-05.


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
- **Expanded search**: Use expanded search when you need to find specific jobs quickly (for example by job ID, host, user, **project**, queue, time window, node count, node-hours, or derived metric thresholds), then open the matching row from the filtered results. The **?** help icon next to each field defines the parameter. Derived metric choices match the **Metrics** tab on each job detail page, and those filters search prepared job-level metric summaries. Multiple criteria are combined with **AND** semantics (all active filters must match). **Job end time** fields filter on scheduler end time, not submit or start time alone.
- **Find Job** (navbar): Enter a job ID to open that job’s detail page directly in the browser. Host-based lookup from the navbar routes to host-centric views in the SPA (there is no separate JSON redirect endpoint for search).
- **Active filter summary**: When a list is filtered, a summary bar above the table shows the active criteria in plain language. Use **Modify search** to reopen expanded search with the same parameters pre-filled.
- **Empty results**: When no jobs match the current filters, the list stays on the page with a **No jobs match these filters** message in the table (this is not an error page). Adjust filters via **Modify search** or clear criteria from expanded search.
- **Job list table**: Typical columns include job ID, submit/start/end times, **runtime**, **requested time (timelimit)**, resource shape (**nodes**, **cores**), **user**, **project**, **queue**, **state**, and **job name**. Row **background color** reflects completion state (e.g. completed vs failed vs other).
- **Histograms** (where configured): Distribution thumbnails for metrics such as **runtime**, **node count**, and **queue wait** help you see whether your job is typical for that filter.
- **Performance Data** column: Short status labels (e.g. summary available, monitoring gaps, not summarized yet) indicate current data readiness for each row.

---

## 2. What you should do first on a job page

- **Breadcrumbs** at the top link back to your filtered job list (when you arrived from search) and show where you are in the site. Use them instead of the browser back button when you want to return to the same filter context.
- **Shareable analysis tabs**: Job data tabs (Summary plot, Roofline, Multiprecision Mix, and related analysis panels) sync to the URL as `?tab=…` so you can bookmark or share a link that opens the same tab. If a plot or metric panel fails to load on first try, the page offers **retry** actions for that section without reloading the whole job.
- **Print**: Use the **Print** button next to the job title to prepare a printable snapshot (Job overview, Full scheduling record, Resources, Metrics, Summary plot, Roofline, and Multiprecision Mix). The browser print dialog opens so you can **Save as PDF** or print on paper—useful for tickets, design reviews, and offline sharing. Processes, Execution and hosts, and Device data are not included in that snapshot.

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
| **Print**                       | Browser print / Save as PDF of overview, scheduling, Resources, Metrics, Summary, Roofline, Multiprecision Mix | Capture a triage packet for support tickets or offline review without copying each tab by hand. |
| **Job overview**                | Compact high-value fields (jid, status, runtime, queue, user/project, cores/nodes, start/end)    | Fast triage before deeper telemetry checks.                                                                                                                    |
| **Full scheduling record**      | Expanded accounting table with all core scheduler columns                                          | Audit exact scheduler/accounting values and formatting without leaving the page.                                                                               |
| **Resources**                   | Rounded Cards in order: watt-hours (when present) → **GPU Information** (aggregate stats as the main line; per-(host,dev) inventory behind a collapsed control) → **Shared File Systems** (`fsio`) → Client/Server log links last. GPU precedence NVIDIA → AMD → Intel PVC. | Validate energy, GPU allocation vs activity (expand inventory for device rows), I/O<sup>[11](#ref-11)</sup> volume, then jump to external logs. |
| **Metrics (tab)**               | Job-level metrics catalog (`metrics_list`) in rounded Cards per subsection **CPU → GPU → File System → Network → Misc** by monitor/catalog `type` (Memory/NUMA under CPU; IB/OPA/Ethernet/LNET under Network). Empty GPU/File System/Misc omitted; **Network** always appears (empty body: Data not available.) | Faster subsystem triage—scan CPU vs fabric vs GPU scalars without scrolling a flat list. |
| **Summary plot (tab)**          | Host-level timeline plot with CPU, memory/NUMA<sup>[4](#ref-4)</sup>/DRAM, fabric/filesystem, GPU, and node-power traces | Best first visual scan for phase changes, host outliers, and cross-signal coupling (for example GPU drops while fabric spikes).                              |
| **Roofline (tab)**              | CPU roofline and GPU roofline (Memory BW when available, else PCIe/NVLink<sup>[7](#ref-7)</sup>)                                                        | Distinguish compute-limited vs bandwidth/link-limited behavior and prioritize the right optimization work.                                                     |
| **Multiprecision Mix (tab)**    | CPU and GPU precision-activity panels over time                                                    | Verify whether the run is using expected mixed-precision paths<sup>[6](#ref-6)</sup> and detect precision mix drift across runs or code versions.                                         |
| **Processes (tab)**             | Process name first; expandable groups of hosts with average Peak VM/HWM/Stack/Text/Libs/Threads on the group header | Confirm what actually executed and its memory footprint (wrappers, launch depth, wrong env, etc.). |
| **Execution and hosts (tab)**   | XALT execution path/cwd/libset and host list                                                       | Environment drift, module/library mismatches, and host-level forensics.                                                                                       |
| **Device data (tab)**           | Device type names and recorded performance events, with links to type-detail pages                  | Discover collected counter families and drill into per-type analysis.                                                                                          |


### 4.1 Device data table (job page)

Lists each monitor **type** name present for the job (for example `host_cpu`, `host_mem`, `nvidia_gpu`, `host_ib`, `llite`, and hardware counter families) and the **event/column names** recorded. Older jobs may still show legacy names such as `cpu` or `mem` alongside or instead of canonical names. **Click the type name** to open the **type detail** page. GPU memory counters may appear as `gpu_mem_util`, `gpu_mem_used_mb`, or legacy `mem_util` depending on when the job ran.

### 4.2 Type detail page

- **Plot**: Rates aggregated over devices for that type (Bokeh).
- **Table** (“Counts aggregated…”): Time-bucketed means across hosts/devices for each column—useful when you want **numeric export-style** inspection without hovering the plot.

### 4.3 Host plot

**Host-centric** view for a **time window you choose** (defaults to roughly the last day if you do not narrow it). Use it for **debugging a specific node** outside a single job context (noisy neighbor, hardware issue, or post-mortem on a login or service node if monitored).

---

## 5. Job-level metrics catalog

This section lists the metrics shown in the Job detail **Metrics** tab and how to interpret them.

On the page, rows are grouped under source subsections in fixed order **CPU → GPU → File System → Network → Misc**. Memory and NUMA metrics appear under **CPU**. Fabric metrics (InfiniBand, Omni-Path, Ethernet, LNET) appear under **Network**, which stays visible even when no network rows exist. GPU, File System, and Misc headings appear only when that section has at least one row. Within each subsection, order follows the API catalog (valued metrics before insufficient / not-computed).

| Metric key | Short label | What it summarizes | Diagnostic / performance interpretation |
| ---------- | ----------- | ------------------ | --------------------------------------- |
| `avg_blockbw` | Average local block-device throughput | Mean local block-device throughput | High values indicate local scratch/checkpoint pressure; unexpected nonzero can reveal spill to local disk. |
| `avg_cpuusage` | Average CPU cores in use | Job-total busy cores (sum of per-host means from user/system/nice); compare with allocated `ncores`. Rows from older pipelines may still be mean-per-host until metrics recompute. | Low vs allocated cores suggests under-subscription, waiting, serialization, or I/O/network stalls. |
| `avg_sharedfs_iops` | Average shared filesystem operation rate | Mean shared filesystem metadata/op rate (Lustre + NFS summed when both present). Insufficient Data here is a coverage gate, not “no FSIO”. | High with low MB/s points to small-file metadata bottlenecks. |
| `avg_sharedfs_bw` | Average shared filesystem read+write bandwidth | Mean shared filesystem bandwidth (Lustre + NFS summed when both present). Insufficient Data here is a coverage gate, not “no FSIO”. | Sustained high values indicate file I/O-heavy phases; correlate with runtime spikes/checkpoint windows. |
| `avg_ibbw` | Average high-speed fabric bandwidth | Mean InfiniBand/fabric byte throughput | High values with modest FLOP rate imply communication-heavy behavior<sup>[15](#ref-15)</sup>. |
| `avg_fabric_mb_per_gflops` | Fabric traffic per floating-point work | Fabric MB per GFLOP | Communication intensity relative to compute; rising with scale often means weaker scaling efficiency. |
| `avg_tensor_active` | Average GPU tensor-pipe activity | Mean lumped tensor pipeline activity (prefer IMMA/HMMA/DFMA splits when present) | Low on expected tensor workloads suggests kernels not reaching tensor paths. |
| `avg_tensor_imma_active` | Average GPU tensor IMMA (INT8/INT4) activity | Mean IMMA tensor-pipe busy fraction | INT8/INT4 tensor path share for mixed-precision kernels. |
| `avg_tensor_hmma_active` | Average GPU tensor HMMA (FP16/BF16) activity | Mean HMMA tensor-pipe busy fraction | FP16/BF16 tensor-core share versus scalar FP pipes. |
| `avg_tensor_dfma_active` | Average GPU tensor DFMA (FP64) activity | Mean DFMA tensor-pipe busy fraction | FP64 tensor path versus scalar FP64. |
| `avg_fp16_active` | Average GPU FP16 pipeline activity | Mean GPU FP16 pipeline activity | Confirms whether mixed-precision execution is using FP16-heavy kernels as expected. |
| `avg_fp32_active` | Average GPU FP32 pipeline activity | Mean GPU FP32 pipeline activity | Tracks single-precision dominant GPU phases and precision-policy drift across runs. |
| `avg_fp64_active` | Average GPU FP64 pipeline activity | Mean GPU FP64 pipeline activity | Surfaces double-precision-heavy GPU work that may reduce peak throughput. |
| `avg_gpu_mem_bw_gbps` | Average GPU memory bandwidth | Mean GPU memory-bandwidth rate | High with moderate utilization can indicate memory-bound kernels. |
| `avg_fabric_mb_per_avg_tensor` | Fabric bandwidth per tensor activity | Fabric MB per average tensor activity | Communication intensity normalized by tensor activity for GPU+MPI workloads. |
| `avg_flops` | Average floating-point throughput | Mean achieved FLOP rate | Baseline compute throughput for CPU-side arithmetic. |
| `avg_flops64b` | Average double-precision FLOP rate | Mean FP64 GFLOP/s from Intel FP_ARITH doubles, or Grace scalar double when Intel absent | FP64 share of busy ops in Multiprecision Mix. |
| `avg_flops32b` | Average single-precision FLOP rate | Mean FP32 GFLOP/s from Intel FP_ARITH singles, or Grace scalar single when Intel absent | FP32 share of busy ops in Multiprecision Mix. |
| `avg_arm_int8_ops` | Average CPU INT8 operation rate | Mean INT8 Gops/s from host_cpu_hw `arm_int8_ops` | INT8 busy-ops share on CPU Multiprecision Mix (not in `avg_flops`). |
| `avg_arm_int16_ops` | Average CPU INT16 operation rate | Mean INT16 Gops/s from host_cpu_hw `arm_int16_ops` | INT16 busy-ops share on CPU Multiprecision Mix (not in `avg_flops`). |
| `avg_mbw` | Average DRAM memory bandwidth | Mean DRAM bandwidth | High with low FLOPs suggests memory-bound CPU phases. |
| `avg_freq` | Average effective CPU frequency | Mean CPU frequency | Drops may indicate power/thermal policy or throttling. |
| `avg_ethbw` | Average Ethernet bandwidth | Mean Ethernet bandwidth | Useful for TCP/object-store workflows that bypass IB paths<sup>[16](#ref-16)</sup>. |
| `detail_gpu_active` | GPUs with non-zero utilization | Number of active GPUs | Lower than allocated GPUs usually means mapping/launcher inefficiency. |
| `detail_gpu_util_max` | Sum of per-GPU peak utilization | Max of per-GPU peaks, shown as value out of GPUs×100 | Peak headroom check; high max with low mean often indicates bursty kernels. |
| `detail_gpu_util_mean` | Sum of per-GPU mean utilization | Sum of per-GPU means, shown as value out of GPUs×100 | Primary “are GPUs doing work?” scalar for the job. |
| `detail_gpu_count` | Total GPUs on job | Total GPUs allocated | Sanity check against scheduler request and host topology. |
| `detail_fsio_llite_read_mb` | Total Lustre client read volume | Total Lustre llite read MB | Aggregate client-side read volume for Lustre path. |
| `detail_fsio_llite_write_mb` | Total Lustre client write volume | Total Lustre llite write MB | Aggregate client-side write volume for Lustre path. |
| `detail_fsio_llite_peak_mb_s` | Peak Lustre client read+write rate | Peak aggregate Lustre client MB/s | Short burst combined read+write throughput versus job-total MB. |
| `detail_fsio_llite_peak_iops` | Peak Lustre client metadata operation rate | Peak aggregate Lustre metadata IOPS | Burst metadata load versus sustained streaming. |
| `detail_fsio_nfs_read_mb` | Total NFS client read volume | Total NFS read MB | Aggregate client-side read volume for NFS-backed paths. |
| `detail_fsio_nfs_write_mb` | Total NFS client write volume | Total NFS write MB | Aggregate client-side write volume for NFS-backed paths. |
| `detail_fsio_nfs_peak_mb_s` | Peak NFS client read+write rate | Peak aggregate NFS client MB/s (shown alongside Lustre when both have data) | Short burst NFS throughput; dual NFS+Lustre rows appear when both clients report volume. |
| `detail_fsio_nfs_peak_iops` | Peak NFS client I/O operation rate | Peak aggregate NFS read/write op rate (alongside Lustre when both present) | Burst small-file or metadata-heavy NFS phases. |
| `avg_gpuutil` | Job GPU utilization (aggregate) | Same aggregate as `detail_gpu_util_mean` when both exist (duplicate row hidden in UI) | Core accelerator utilization KPI; low values indicate feed/scheduling inefficiency. |
| `job_cpu_gpu_watt_hours` | CPU+GPU watt-hours for job (CPU watt-hours when no GPUs) | ∫ estimated node power over time, summed across hosts (Wh); GPU included when present | Top of Resources when CPU power fragments exist; title omits +GPU when `gpu_count` is absent/zero; energy budget for the run. |
| `avg_packetsize` | Mean fabric packet payload size | Mean network packet size | Small average packet sizes imply metadata/collective chatter overhead. |
| `max_fabricbw` | Peak fabric data rate | Peak fabric MB/s on the same conversion basis as `avg_ibbw` (not packet-rate scale) | Captures communication bursts that may not appear in averages. |
| `max_lnetbw` | Peak Lustre LNET client data rate | Peak Lustre LNet bandwidth | Peak parallel file-system network pressure. |
| `max_mds` | Peak shared filesystem metadata operation rate | Peak metadata operation rate | High peaks indicate metadata storms (create/unlink/readdir heavy phases). |
| `max_packetrate` | Peak fabric packet rate | Peak packet rate | High with small packet size suggests message-rate overhead. |
| `max_opa_congestion_rate` | Peak Omni-Path congestion event rate | Peak OPA congestion-related counter rate | OPA-specific network contention indicator. |
| `max_numa_remote_rate` | Peak non-local NUMA memory access rate | Peak NUMA remote-access rate | High values indicate locality/memory placement issues<sup>[4](#ref-4)</sup>. |
| `max_gpu_power` | Maximum GPU power draw | Peak GPU power draw | Detects power-cap proximity or thermal stress windows. |
| `max_node_power_est_w` | Peak estimated node power | Peak estimated node power | Useful for peak power envelope checks and cooling stress. |
| `avg_node_power_est_w` | Mean estimated node power | Mean estimated node power | Energy-to-solution comparisons across runs/configurations. |
| `max_gpu_link_gbps` | Peak GPU PCIe and NVLink data rate | Peak GPU link bandwidth (PCIe/NVLink aggregate path) | Host-device/device-device transfer pressure indicator. |
| `max_gpu_clock_event_reasons` | Peak GPU clock throttling reasons | DCGM clock-event bitmask (Job Detail shows decoded flag names; API/search stay numeric) | Named flags (power cap, thermal, sync boost, …) explain why clocks were limited; correlate with power/temp traces. |
| `mem_hwm` | Peak process resident memory (high water mark) | High-water memory estimate (MemUsed-Slab-FilePages) | Compare with node RAM for host OOM risk<sup>[14](#ref-14)</sup>. |
| `node_imbalance` | CPU utilization imbalance across nodes | Node-level CPU rate imbalance | High values indicate decomposition/rank imbalance. |
| `time_imbalance` | CPU rate imbalance over job timeline | Temporal CPU imbalance across job timeline | Flags long underutilized windows or phase imbalance over time. |
| `flops_node_imbalance` | Floating-point rate imbalance across nodes | Node-level FLOP rate imbalance | Compute work unevenly distributed across nodes. |
| `fabric_node_imbalance` | Fabric bandwidth imbalance across nodes | Node-level fabric traffic imbalance | Some ranks/nodes communicate disproportionately. |
| `dram_bw_node_imbalance` | DRAM bandwidth imbalance across nodes | Node-level DRAM bandwidth imbalance | Memory pressure concentrated on subset of nodes. |
| `lnet_node_imbalance` | LNET bandwidth imbalance across nodes | Node-level LNet imbalance | Uneven filesystem/network load distribution. |
| `gpu_util_node_imbalance` | GPU utilization imbalance across nodes | Node-level GPU utilization imbalance | Multi-node training/inference skew across nodes. |
| `tensor_node_imbalance` | Tensor-pipe activity imbalance across nodes | Node-level tensor-activity imbalance | Tensor kernels unevenly distributed across participating nodes. |
| `vecpercent_64b` | Double-precision vector FLOP share (%) | Percent (0–100) of double-precision FLOPs done via vector widths > scalar | Low values on DP-heavy code suggest SIMD/vectorization opportunity<sup>[5](#ref-5)</sup>. |
| `avg_vector_width_64b` | Effective vector width (double precision) | Average effective DP vector width (UI may combine with SP as one DP/SP row) | Closer to scalar indicates weak SIMD utilization in DP paths. |
| `vecpercent_32b` | Single-precision vector FLOP share (%) | Percent (0–100) of single-precision FLOPs done via vector widths > scalar | Low values on SP-heavy code suggest vectorization opportunity<sup>[5](#ref-5)</sup>. |
| `avg_vector_width_32b` | Effective vector width (single precision) | Average effective SP vector width (UI may combine with DP as one DP/SP row) | Low average width indicates scalar/short-vector dominated SP execution. |

---

## 6. Data/plot surfaces and what they mean diagnostically

This section covers job-detail surfaces beyond scalar metrics.

### 6.1 Summary plot

- Diagnostic use: fastest phase/host outlier scan across CPU, memory, network, I/O, and GPU traces; the bottom **Hardware error rates** panel overlays job-wide sums of InfiniBand, Ethernet, and OPA error counter rates when those streams exist.
- Time axis and hover use the cluster timezone from site configuration (not raw UTC labels).
- GPU tensor traces: when IMMA/HMMA/DFMA splits are present, Summary shows separate tensor-pipe subplots for those kinds (preferred over a single lumped tensor-pipe series). The lumped any-pipe tensor series appears only when those splits are absent.
- Performance recommendation: always pair with Metrics tab; peaks in summary often explain extreme scalar maxima and telemetry behavior<sup>[12](#ref-12)</sup>.

### 6.2 Roofline tab (CPU + GPU)

- Diagnostic use: distinguish compute-ceiling vs bandwidth/link-ceiling regimes in the roofline model<sup>[1](#ref-1)</sup>.
- Recommendation: use `avg_flops`<sup>[3](#ref-3)</sup>, `avg_mbw`, `avg_gpu_mem_bw_gbps`, `max_gpu_link_gbps`, and fabric ratios to validate roofline reading.
- CPU vs GPU regime quick read:
  - **CPU roofline**: if points sit near the sloped bandwidth line, the phase is memory-bandwidth limited (data movement dominates); if points approach the flat top line, compute throughput dominates.
  - **GPU roofline**: the plot title shows which bandwidth axis was used — **GPU Roofline (Memory BW)** when estimated GPU memory bandwidth samples are present (same family of signal as Summary HBM BW), otherwise **GPU Roofline (PCIe/NvLink)** from interconnect byte rates. Apply the same compute vs bandwidth logic; low arithmetic intensity<sup>[2](#ref-2)</sup> phases are often HBM- or link-limited depending on that title, while high-intensity phases can become tensor/core compute limited.
  - **Interconnect context**: GPU scaling limits can reflect PCIe or NVLink behavior<sup>[7](#ref-7)</sup>, not just kernel math throughput — especially when the PCIe/NvLink title is shown.
  - **What to do with this**: memory/link-limited phases usually benefit from locality, data-layout, batching, or communication changes; compute-limited phases usually benefit from kernel efficiency, vector/tensor usage, and occupancy improvements.

### 6.3 Multiprecision Mix tab (CPU and GPU)

- Diagnostic use: quantify mixed-precision path composition (DP/SP/tensor, and CPU INT16/INT8 when present) as a share of **busy** arithmetic rates only (no idle wedge)<sup>[6](#ref-6)</sup>.
- CPU pie: wedges come from rate-weighted FP64/FP32 (`avg_flops64b` / `avg_flops32b`, Intel or ARM scalar) plus INT16/INT8 (`avg_arm_int16_ops` / `avg_arm_int8_ops`) when positive — a busy-ops mix, not identical physical units; not vectorization-within-width percentages.
- GPU pie: hover shows share of busy percent; wedges label Tensor IMMA (INT8/INT4), Tensor HMMA (FP16/BF16), and Tensor DFMA (FP64) when those splits are present (preferred over lumped tensor activity). CPU half/bf16/tf32/fp8 appear only when the monitor emits them.
- Width behavior: each pie includes the precision widths that have usable positive metrics for that job/architecture; missing widths are omitted rather than treated as errors.
- Recommendation: when model/code changes precision policy, compare this tab first, then check throughput/utilization deltas.

### 6.4 Resources panel (FSIO + GPU summary + logs)

- Diagnostic use: rapid verification of I/O volume, burst peaks, and GPU occupancy before deep plotting. Lustre and NFS resource rows can both appear when both clients have data for the job.
- Recommendation: if FS totals or **Peak MB/s** / **Peak IOPS** are high, check `FS MB/s`, `FS IOPS`, `MDS peak`, and LNET metrics for bottleneck type; this is usually an I/O bottleneck triage path<sup>[11](#ref-11)</sup>. GPU statistics sit above the log buttons so you scan filesystem and accelerator context together before opening logs.

### 6.5 Execution and hosts tab

- Diagnostic use: detect environment drift (wrong module/library/container path) and host-specific anomalies.
- Recommendation: for regressions with “same script,” verify this tab before tuning code.

### 6.6 Device data tab

- Diagnostic use: confirms which counter families/events were actually collected for this job.
- Recommendation: use this tab to confirm expected telemetry families when interpreting plots/metrics.
- Note: large jobs with many samples may take a long time to load; report timeouts to support.

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
| 2026-05-07 | Job Detail summary hardware error overlay, screen-space Bokeh help on Job Detail plots, FSIO peak columns and catalog metrics, Resources layout (GPU summary above log links), expanded-search help note. |
| 2026-05-07 | Added GPU precision activity catalog entries (`avg_fp16_active`, `avg_fp32_active`, `avg_fp64_active`) and documented that Multiprecision Mix pies render whatever precision widths are available per job/architecture. |
| 2026-06-04 | Documented unified InfiniBand collector typename `host_ib` (replaces separate `ib_ext` / switch types in schema examples). |
| 2026-06-05 | Job list filter summary, empty-result UX, expanded-search AND/end-time semantics, navbar Find Job, job-detail breadcrumbs, shareable `?tab=` analysis tabs, partial-load retry behavior, canonical vs legacy device type names in Device data. |
| 2026-06-05 | Job detail Metrics tab short labels expanded to describe what is measured; units appear only in the bracket suffix beside each row (no unit tokens in label text). |
| 2026-07-20 | Summary plot GPU tensors: IMMA/HMMA/DFMA split time series preferred over lumped tensor-pipe; lumped fallback when splits absent. |
| 2026-07-19 | Dual NFS+Lustre Resources/FSIO; Multiprecision Mix busy-FLOPS-only (CPU FP_ARITH shares, GPU tensor splits + hover %); `avg_cpuusage` job-total busy cores vs `ncores`; fabric peak on same basis as `avg_ibbw`; `avg_sharedfs_*` Insufficient = coverage gate. |
| 2026-07-23 | Job Detail: Grace→CPU labels; GPU util out of GPUs×100; zero-mean activity ≠ missing; CPU+GPU watt-hours atop Resources; Processes multi-column; combined DP/SP vector width; Summary axis help + shorter labels; Multiprecision pie title/clip. |
| 2026-07-24 | Residuals: pie legend below frame; GPU-link Summary clamp; continuous Summary lines; avg_cpuusage scaled to allocated ncores; TypeDetail 2-col + heading ?; Summary SPA help strip; Processes grouped by name with averages. |
| 2026-07-30 | Metrics tab: source subsections CPU → GPU → File System → Network → Misc; Memory/NUMA under CPU; always-show Network; hide empty GPU/FS/Misc. |
| 2026-07-31 | Resources GPU inventory: one row per device when ingest stores `dev`; legacy empty-`dev` jobs show node-aggregate note (not “per GPU”). |
| 2026-07-31 | Resources/Metrics Card chrome: Watt hours → GPU Information (inventory collapsed) → Shared File Systems → logs; Metrics subsections in Cards. |
| 2026-08-06 | Processes tab columns: Peak VM, HWM, Stack, Text, Libs, Threads (dropped RSS/Size from visible set). |
| 2026-08-05 | GPU Roofline prefers estimated memory bandwidth when present (title Memory BW); otherwise PCIe/NvLink (or Xe Link) with matching title and peak roof. |
| 2026-08-05 | Job Detail **Print** button: prepares overview/Resources/Metrics/Summary/Roofline/Multiprecision Mix for the browser print dialog (Save as PDF). |


