# Using HPCPerfStats on the Web - Guide for Researchers and HPC Users


This guide is for users and researchers working on clusters tracked by HPCPerfStats and focuses on using HPCPerfStats website data to understand application runtime performance and diagnostics; it was last updated on 2026-11-26.


This document is ordered so the **most decision-relevant ideas come first**. Deeper catalog-style detail appears in later sections.

> **Data availability note:** Not all data appears for every job or architecture. When data is missing, the interface reports this directly in the relevant panel/tab.

## Index

- [1. Finding jobs and reading the job list](#1-finding-jobs-and-reading-the-job-list)
- [2. What you should do first on a job page](#2-what-you-should-do-first-on-a-job-page)
- [3. Job detail - scheduler and accounting fields](#3-job-detail---scheduler-and-accounting-fields)
- [4. Supporting panels on the job page](#4-supporting-panels-on-the-job-page)
- [5. Job-level metrics catalog](#5-job-level-metrics-catalog)
- [6. Data/plot surfaces and what they mean diagnostically](#6-dataplot-surfaces-and-what-they-mean-diagnostically)
- [7. Failure-mode checklist](#7-failure-mode-checklist)
- [8. Appendix - concept guide with external references](#8-appendix---concept-guide-with-external-references)
- [9. Related references in this repository](#9-related-references-in-this-repository)

---

## 1. Finding jobs and reading the job list

- **Search home**: Browse by **year** or **date** to reach filtered job lists.
- **Extended search** (navbar): Use **Extended search** when you need to find specific jobs quickly (for example by job ID, host, user, **project**, state, queue, job end time window, runtime, node count, node-hours, or derived metric thresholds), then open the matching row from the filtered results. The **?** help icon next to each field defines the parameter. Derived metric choices match the **Metrics** tab on each job detail page, and those filters search prepared job-level metric summaries. Multiple criteria are combined with **AND** semantics (all active filters must match). **Job end time** fields filter on scheduler end time, not submit or start time alone.
- **Find Job** (navbar): Enter a job ID to open that job's detail page directly. In **Extended search**, a job ID also opens that one job, and a **host** together with a start of the job end time window opens the host plot for that host (section 4.3) instead of a job list.
- **Active filter summary**: When a list is filtered, an **Active filters** box above the table shows the active criteria in plain language. Use **Modify search** to reopen **Extended search**.
- **Empty results**: When no jobs match the current filters, the list stays on the page with a **No jobs match these filters** message in the table (this is not an error page). Adjust filters via **Modify search** or clear criteria from **Extended search**.
- **Job list table**: Columns are **Job ID**, **Performance data**, **User**, **Project**, **nodes**, **cores**, **run time (HH:MM:SS)** (hours may exceed 24 for long jobs), **start time**, **end time**, **queue**, **status**, **node hrs**, and **name** (job name).
- **Histograms**: Distribution thumbnails for **runtime**, **node count**, and **queue wait** help you see whether your job is typical for that filter.
- **Performance data** column: Short status labels indicate readiness for each row. **Metrics & Plots available** means metrics and plots are ready. **Metrics available** means metrics values exist but plots are still preparing. **Too short to complete**, **Too few samples to complete**, and **Metrics & Plots not yet completed** cover jobs that are too short, have too few samples, or have not yet gone through metrics/plot computation.

---

## 2. What you should do first on a job page

- **Breadcrumbs** at the top (**Browse** > **Job &lt;id&gt;**) show where you are in the site; **Browse** returns to the search home.
- **Shareable analysis tabs**: Job data tabs (Metrics, Summary plots, Roofline, Multiprecision Mix, Processes, Execution and hosts, Device data) sync to the URL as `?tab=...` so you can bookmark or share a link that opens the same tab. The Summary plots, Roofline, and Multiprecision Mix tabs load only when Performance data is **Metrics & Plots available**; otherwise the tab shows **Plots not yet completed.** (still preparing) or **No plots available for this job** (too short / too few samples). Metrics and other non-plot tabs never show plot loading banners. If a plot panel fails to load after it is ready, the page offers **Retry plots** for that section without reloading the whole job.
- **Print**: Use the **Print** button next to the breadcrumbs to prepare a printable snapshot (Job overview, Resources, Metrics, Summary plots, Roofline, and Multiprecision Mix). The browser print dialog opens so you can **Save as PDF** or print on paper - useful for tickets, design reviews, and offline sharing. Processes, Execution and hosts, Device data, and the Client / Server log links are not included in that snapshot. When Performance data is **Metrics available** (plots still preparing), the snapshot leaves out the plot tabs; for jobs without metrics, Print says **There is no data to print.**

1. **Read Job overview first** (status, runtime, queue, cores/nodes, user/project, start/end). This frames the run before touching telemetry.
2. **Open the Summary plots tab** in **Job data**. It is the fastest way to see phase changes and node-to-node divergence across CPU, memory, I/O<sup>[11](#ref-11)</sup>, network, and GPU signals.
3. **Open the Roofline tab** for performance-ceiling context: it contains **both** CPU roofline and GPU roofline<sup>[1](#ref-1)</sup> (GPU roofline appears when required counters exist).
4. **Open the Multiprecision Mix tab** to check CPU/GPU precision activity composition.
5. **Open the Metrics tab** for scalar job-level summaries (averages, peaks, imbalance percentages, detail GPU/FSIO rows). Many rows have a **help icon** for definitions.
6. **Use Device data tab** to jump into per-type detail pages (`/machine/job/<jid>/<type>/`) for raw event families and type-level plots/tables.
7. **Use Processes and Execution and hosts tabs** when you need command-line provenance, XALT library context, and host list verification.

**Shared nodes:** When a job shared a node with other jobs, CPU and GPU metrics and plots are limited to that job's cores and GPU devices when those are known, and memory summaries prefer the job's cgroup memory. On such a job, the **?** help for a counter that stays node-wide ends with a **red** note saying the counter is node-wide and not limited to this job; help for job-scoped CPU, GPU, and cgroup memory signals has no such note. Exclusive (sole-occupancy) jobs never show that note.

---

## 3. Job detail - scheduler and accounting fields

These fields appear in **Job overview**. They come from batch accounting (e.g. Slurm) and define the **official** story of the run.


| Field                  | What it is                | Diagnostic use                                                                                                                             |
| ---------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **Job ID**             | Unique scheduler id       | Cross-reference logs, support tickets, and reproducer scripts.                                                                             |
| **User**               | Submitter                 | Ownership and fair-share context.                                                                                                          |
| **Project**            | Charge or allocation      | Billing and policy (which allocation was charged).                                                                                         |
| **Start / End**        | Scheduler window          | Align with external logs; detect unexpected early end.                                                                                     |
| **Run time (s)**       | End - start (as recorded) | Compare to the time limit you requested: jobs that always use much less than the limit may be over-requesting; jobs hitting the limit may be **timing out**. |
| **Queue**              | Partition / class         | Explains hardware policy (GPU vs CPU, debug vs production).                                                                                |
| **Job name**           | User-provided label       | Often encodes experiment id or binary name - quick sanity check.                                                                             |
| **Status**             | Completion outcome        | **Failed** jobs may still have partial telemetry - still worth opening.                                                                      |
| **Cores / nodes** (cores) | Core count allocated   | Match to how you launched MPI<sup>[9](#ref-9)</sup>/OpenMP<sup>[10](#ref-10)</sup> (miscounts -> wrong binding).                                                                          |
| **Cores / nodes** (nodes) | Node count             | Multi-node imbalance metrics and network plots only make sense in this context.                                                            |


---

## 4. Supporting panels on the job page


| Panel / tab                     | Content                                                                                           | Diagnostic use                                                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Print**                       | Browser print / Save as PDF of Job overview, Resources, Metrics, Summary plots, Roofline, Multiprecision Mix | Capture a triage packet for support tickets or offline review without copying each tab by hand. |
| **Job overview**                | Compact high-value fields (Job ID, status, runtime, queue, user/project, cores/nodes, start/end)    | Fast triage before deeper telemetry checks.                                                                                                                    |
| **Resources**                   | Cards in order: watt-hours (when present) -> **GPU Information** (aggregate stats as the main line; per-host, per-device inventory behind a collapsed control) -> **Shared File Systems** -> **Client / Server logs** links last. GPU precedence NVIDIA -> AMD -> Intel. | Validate energy, GPU allocation vs activity (expand inventory for device rows), I/O<sup>[11](#ref-11)</sup> volume, then jump to external logs. |
| **Metrics (tab)**               | Job-level metrics catalog in one card per subsection **CPU -> GPU -> File System -> Network -> Misc** (Memory/NUMA under CPU; IB/OPA/Ethernet/LNET under Network). Each section shows valued metrics first; metrics without a numeric value sit in a default-collapsed **Metrics not computed** table at the bottom of that section. Empty CPU/GPU/File System/Misc omitted; **Network** always appears (empty body: Data not available.) | Faster subsystem triage - scan CPU vs fabric vs GPU scalars without scrolling past unavailable rows; expand **Metrics not computed** when you need to see which signals are missing. |
| **Summary plots (tab)**          | Host-level timeline plot with CPU, memory/NUMA<sup>[4](#ref-4)</sup>/DRAM, fabric/filesystem, GPU, and node-power traces | Best first visual scan for phase changes, host outliers, and cross-signal coupling (for example GPU drops while fabric spikes).                              |
| **Roofline (tab)**              | CPU roofline and GPU roofline (Memory BW when available, else PCIe/NVLink<sup>[7](#ref-7)</sup>)                                                        | Distinguish compute-limited vs bandwidth/link-limited behavior and prioritize the right optimization work.                                                     |
| **Multiprecision Mix (tab)**    | CPU and GPU precision-activity panels over time                                                    | Verify whether the run is using expected mixed-precision paths<sup>[6](#ref-6)</sup> and detect precision mix drift across runs or code versions.                                         |
| **Processes (tab)**             | Process name first; expandable groups of hosts with average Peak VM/HWM/Stack/Text/Libs/Threads on the group header. Peak VM/HWM/Stack/Text/Libs are shown in **MB** and are the **job-level high water** for that process name (the largest value seen across the job's samples). | Confirm what actually executed and its memory footprint (wrappers, launch depth, wrong env, etc.). |
| **Execution and hosts (tab)**   | XALT execution path/cwd/libset and host list                                                       | Environment drift, module/library mismatches, and host-level forensics.                                                                                       |
| **Device data (tab)**           | Device type names and recorded performance events, with links to type-detail pages                  | Discover collected counter families and drill into per-type analysis.                                                                                          |


### 4.1 Device data table (job page)

The **Type Name** column lists each monitor **type** present for the job (for example `host_cpu`, `host_mem`, `nvidia_gpu`, `host_ib`, `lustre_llite`, and hardware counter families) and the **Recorded Performance Events** column lists the event names recorded. Jobs recorded by older monitors list legacy type names such as `cpu` or `mem` alongside or instead of canonical names. **Click the type name** to open the **type detail** page. GPU memory counters appear as `gpu_mem_util` and `gpu_mem_used_mb`; jobs recorded by older monitors show the legacy name `mem_util`.

### 4.2 Type detail page

- **Plot** ("Rates Aggregated over devices"): Rates aggregated over devices for that type.
- **Table** ("Counts Aggregated over devices and hosts"): Means across hosts/devices for each event at each time offset from the first sample-useful when you want **numeric export-style** inspection without hovering the plot.

### 4.3 Host plot

**Host-centric** view for a **time window you choose** (defaults to roughly the last day if you do not narrow it). Use it for **debugging a specific node** outside a single job context (noisy neighbor, hardware issue, or post-mortem on a login or service node if monitored).

---

## 5. Job-level metrics catalog

This section lists the metrics shown in the Job detail **Metrics** tab and how to interpret them.

On the page, rows are grouped under source subsections in fixed order **CPU -> GPU -> File System -> Network -> Misc**. Memory and NUMA metrics appear under **CPU**. Fabric metrics (InfiniBand, Omni-Path, Ethernet, LNET) appear under **Network**, which stays visible even when no network rows exist. CPU, GPU, File System, and Misc headings appear only when that section has at least one row. Within each subsection, metrics with a value come first; metrics without a value sit in the default-collapsed **Metrics not computed** table at the bottom of that subsection.

**Why a metric has no value.** A row without a value says **Data not available.** unless the page can tell you something you can act on. Two explanations are shown to everyone:

- **"<collector> collector not found on this node's monitor"** (for example `host_ib collector not found on this node's monitor`): the monitoring on your job's nodes does not collect that family at all, such as a node without an InfiniBand adapter. Nothing went wrong with your job; that metric simply does not exist there.
- **"<collector>: counter not present (n/N hosts)"**: the collector runs, but the specific hardware counter does not exist on n of your N nodes (for example a counter that this CPU model or adapter does not provide). Compare with jobs on other node types before reading anything into the gap.

Other causes (a read that failed, counters busy with another tool, a monitor update in progress) are temporary site-side conditions. They are shown as **Data not available.**, and the site team sees the details. When such a gap is short, the value is still computed from the time the counters were readable, so you see the number without a note.

| Metric key | Short label | What it summarizes | Diagnostic / performance interpretation |
| ---------- | ----------- | ------------------ | --------------------------------------- |
| `avg_blockbw` | Average local block-device throughput | Mean local block-device throughput | High values indicate local scratch/checkpoint pressure; unexpected nonzero can reveal spill to local disk. |
| `avg_cpuusage` | Average CPU cores in use | Job-total busy cores (sum of per-host means from user/system/nice); compare with allocated `ncores`. Jobs whose metrics an older pipeline computed and that have not been recomputed show the mean per host instead. | Low vs allocated cores suggests under-subscription, waiting, serialization, or I/O/network stalls. |
| `avg_sharedfs_iops` | Average shared filesystem operation rate | Mean shared filesystem metadata/op rate (Lustre + NFS + BeeGFS summed when present). | High with low MB/s points to small-file metadata bottlenecks. |
| `avg_sharedfs_bw` | Average shared filesystem read+write bandwidth | Mean shared filesystem bandwidth (Lustre + NFS + BeeGFS summed when present). | Sustained high values indicate file I/O-heavy phases; correlate with runtime spikes/checkpoint windows. |
| `avg_ibbw` | Average high-speed fabric bandwidth | Mean InfiniBand/fabric byte throughput | High values with modest FLOP rate imply communication-heavy behavior<sup>[15](#ref-15)</sup>. |
| `avg_fabric_mb_per_gflops` | Fabric traffic per floating-point work | Fabric MB per GFLOP | Communication intensity relative to compute; rising with scale often means weaker scaling efficiency. |
| `avg_tensor_active` | Average GPU tensor-pipe activity | Mean lumped tensor pipeline activity (prefer IMMA/HMMA/DMMA splits when present) | Low on expected tensor workloads suggests kernels not reaching tensor paths. |
| `avg_tensor_imma_active` | Average GPU tensor IMMA (INT8/INT4) activity | Mean IMMA tensor-pipe busy fraction | INT8/INT4 tensor path share for mixed-precision kernels. |
| `avg_tensor_hmma_active` | Average GPU tensor HMMA (FP16/BF16/TF32/FP8) activity | Mean HMMA tensor-pipe busy fraction; one counter for FP16, BF16, TF32 and FP8 tensor math | Reduced-precision tensor-core share versus scalar FP pipes; it cannot tell those four precisions apart, so tensor FLOP figures built on it are estimates. |
| `avg_tensor_dmma_active` | Average GPU tensor DMMA (FP64) activity | Mean FP64 tensor-core busy fraction; measured on Hopper and Blackwell nodes, counter not present on A100 | Shows FP64 matrix work (for example DGEMM in GPU math libraries) running on tensor cores rather than the FP64 pipe. |
| `avg_tensor_dfma_active` | FP64 FMA pipe (same as FP64 on Hopper/Blackwell) | Mean FP64 fused multiply-add pipe busy fraction | On Hopper and Blackwell it equals `avg_fp64_active`; read that and `avg_tensor_dmma_active` for FP64 work. It is not a Multiprecision Mix wedge. |
| `avg_fp16_active` | Average GPU FP16 pipeline activity | Mean GPU FP16 pipeline activity | Confirms whether mixed-precision execution is using FP16-heavy kernels as expected. |
| `avg_fp32_active` | Average GPU FP32 pipeline activity | Mean GPU FP32 pipeline activity | Tracks single-precision dominant GPU phases and precision-policy drift across runs. |
| `avg_fp64_active` | Average GPU FP64 pipeline activity | Mean GPU FP64 pipeline activity | Surfaces double-precision-heavy GPU work that may reduce peak throughput. |
| `avg_integer_active` | Average GPU integer pipeline activity | Mean GPU integer pipe busy fraction (not on A100) | High on integer-heavy kernels (hashing, sorting, graph codes). On Hopper the integer pipe is also busy, about half the time, under floating-point work (address and index math), so a busy integer pipe there does not mean the code did little FP work; on Blackwell it stays near 0 under floating-point work. |
| `avg_compute_engine_util` | Average Intel PVC compute engine busy | Mean busy percentage of the Intel Data Center GPU Max (PVC) compute engines, highest tile per sample | On PVC nodes this is the compute engine busy number. Intel PVC jobs that ran on GPU firmware older than PVC2_1.23423 may show incorrect values. Low values on a GPU job mean the tiles sat idle (host-side work, data movement, or too little work per launch); near 100 means the compute engines stayed busy. |
| `avg_gpu_mem_bw_gbps` | Average GPU memory bandwidth | Mean GPU memory-bandwidth rate | High with moderate utilization can indicate memory-bound kernels. |
| `avg_fabric_mb_per_avg_tensor` | Fabric bandwidth per tensor activity | Fabric MB per average tensor activity | Communication intensity normalized by tensor activity for GPU+MPI workloads. |
| `avg_flops` | Average floating-point throughput | Mean achieved FLOP rate (AMD retired FLOPs, Intel floating-point instructions times vector width, Grace measured FLOPs) | Baseline compute throughput for CPU-side arithmetic; on AMD Zen3 / Zen4 nodes it is the only CPU FLOP number (no split by precision). |
| `avg_flops64b` | Average double-precision FLOP rate | Mean FP64 FLOP rate: Intel floating-point instructions times vector width, or AMD Zen5 double-precision FLOPs | Shows how much double-precision arithmetic the code did; the Multiprecision Mix pie uses `avg_fp64_instr` instead. |
| `avg_flops32b` | Average single-precision FLOP rate | Mean FP32 FLOP rate: Intel floating-point instructions times vector width, or AMD Zen5 FLOPs minus double-precision FLOPs | Shows how much single-precision arithmetic the code did; the Multiprecision Mix pie uses `avg_fp32_instr` instead. |
| `avg_fp64_instr` | Average CPU FP64 instruction rate | Mean double-precision floating-point instructions per second (Ginstr/s), all vector widths, on Grace and Intel nodes; on AMD Zen5 nodes double-precision GFLOP/s, the only precision split those CPUs count | FP64 share of the CPU Multiprecision Mix pie; a large share on code meant to run in lower precision points at a precision-policy or library-choice problem. |
| `avg_fp32_instr` | Average CPU FP32 instruction rate | Mean single-precision floating-point instructions per second (Ginstr/s), all vector widths, on Grace and Intel nodes; on AMD Zen5 nodes single-precision GFLOP/s (bfloat16 included) | FP32 share of the CPU pie; confirms single-precision paths are actually running. |
| `avg_fp16_instr` | Average CPU FP16 instruction rate | Mean half-precision floating-point instructions per second (Ginstr/s), all vector widths; counted only on Arm (aarch64) nodes such as NVIDIA Grace, since the x86 collectors do not count FP16 instructions | FP16 share of the CPU pie; shows whether mixed-precision CPU kernels use half precision as intended. |
| `avg_arm_int8_ops` | Average CPU INT8 operation rate | Mean INT8 vector instructions per second (Ginstr/s) | INT8 share of the CPU Multiprecision Mix pie (not in `avg_flops`); useful for quantized inference on the CPU. |
| `avg_arm_int16_ops` | Average CPU INT16 operation rate | Mean INT16 vector instructions per second (Ginstr/s) | INT16 share of the CPU Multiprecision Mix pie (not in `avg_flops`). |
| `avg_mbw` | Average DRAM memory bandwidth | Mean DRAM bandwidth. AMD Milan jobs recorded by monitors without per-channel DF rows read about half (4 of 8 channels counted). | High with low FLOPs suggests memory-bound CPU phases. |
| `avg_freq` | Average effective CPU frequency | Mean CPU frequency | Drops may indicate power/thermal policy or throttling. |
| `avg_ethbw` | Average Ethernet bandwidth | Mean Ethernet bandwidth | Useful for TCP/object-store workflows that bypass IB paths<sup>[16](#ref-16)</sup>. |
| `detail_gpu_active` | GPUs with non-zero utilization | Number of active GPUs | Lower than allocated GPUs usually means mapping/launcher inefficiency. |
| `detail_gpu_util_max` | Sum of per-GPU peak utilization | Max of per-GPU peaks, shown as value out of GPUs x 100 | Peak headroom check; high max with low mean often indicates bursty kernels. |
| `detail_gpu_util_mean` | Sum of per-GPU mean utilization | Sum of per-GPU means, shown as value out of GPUs x 100 | Primary "are GPUs doing work?" scalar for the job. |
| `detail_gpu_count` | Total GPUs on job | Total GPUs allocated | Sanity check against scheduler request and host topology. |
| `detail_fsio_llite_read_mb` | Total Lustre client read volume | Total Lustre llite read MB | Aggregate client-side read volume for Lustre path. |
| `detail_fsio_llite_write_mb` | Total Lustre client write volume | Total Lustre llite write MB | Aggregate client-side write volume for Lustre path. |
| `detail_fsio_llite_peak_mb_s` | Peak Lustre client read+write rate | Peak aggregate Lustre client MB/s | Short burst combined read+write throughput versus job-total MB. |
| `detail_fsio_llite_peak_iops` | Peak Lustre client metadata operation rate | Peak aggregate Lustre metadata IOPS | Burst metadata load versus sustained streaming. |
| `detail_fsio_nfs_read_mb` | Total NFS client read volume | Total NFS read MB | Aggregate client-side read volume for NFS-backed paths. |
| `detail_fsio_nfs_write_mb` | Total NFS client write volume | Total NFS write MB | Aggregate client-side write volume for NFS-backed paths. |
| `detail_fsio_nfs_peak_mb_s` | Peak NFS client read+write rate | Peak aggregate NFS client MB/s (shown alongside Lustre when both have data) | Short burst NFS throughput; dual NFS+Lustre rows appear when both clients report volume. |
| `detail_fsio_nfs_peak_iops` | Peak NFS client I/O operation rate | Peak aggregate NFS read/write op rate (alongside Lustre when both present) | Burst small-file or metadata-heavy NFS phases. |
| `detail_fsio_beegfs_read_mb` | Total BeeGFS client read volume | Total BeeGFS read MB | Aggregate client-side read volume for BeeGFS-backed paths. |
| `detail_fsio_beegfs_write_mb` | Total BeeGFS client write volume | Total BeeGFS write MB | Aggregate client-side write volume for BeeGFS-backed paths. |
| `detail_fsio_beegfs_peak_mb_s` | Peak BeeGFS client read+write rate | Peak aggregate BeeGFS client MB/s | Short burst BeeGFS throughput versus job-total MB. |
| `detail_fsio_beegfs_peak_iops` | Peak BeeGFS client metadata operation rate | Peak aggregate BeeGFS metadata IOPS | Burst metadata load (creates, stats, readdirs) on BeeGFS. |
| `avg_gpuutil` | Job GPU utilization (aggregate) | Same aggregate as `detail_gpu_util_mean` when both exist | Core accelerator utilization KPI; low values indicate feed/scheduling inefficiency. |
| `job_cpu_gpu_watt_hours` | CPU+GPU watt-hours for job (CPU watt-hours when no GPUs) | Integral of estimated node power over time, summed across hosts (Wh); GPU included when present | Shown at the top of Resources as **CPU+GPU Watt Hours for Job**, or **CPU Watt Hours for Job** when the job had no GPUs; energy budget for the run and energy-to-solution comparisons across runs. |
| `avg_packetsize` | Mean fabric packet payload size | Mean network packet size | Small average packet sizes imply metadata/collective chatter overhead. |
| `max_fabricbw` | Peak fabric data rate | Peak fabric MB/s on the same conversion basis as `avg_ibbw` (not packet-rate scale) | Captures communication bursts that may not appear in averages. |
| `max_lnetbw` | Peak Lustre LNET client data rate | Peak Lustre LNet bandwidth | Peak parallel file-system network pressure. |
| `max_mds` | Peak shared filesystem metadata operation rate | Peak metadata operation rate | High peaks indicate metadata storms (create/unlink/readdir heavy phases). |
| `max_packetrate` | Peak fabric packet rate | Peak packet rate | High with small packet size suggests message-rate overhead. |
| `max_opa_congestion_rate` | Peak Omni-Path congestion event rate | Peak OPA congestion-related counter rate | OPA-specific network contention indicator. |
| `max_numa_remote_rate` | Peak non-local NUMA memory access rate | Peak NUMA remote-access rate | High values indicate locality/memory placement issues<sup>[4](#ref-4)</sup>. |
| `max_gpu_power` | Maximum GPU power draw | Peak GPU power draw | Detects power-cap proximity or thermal stress windows. |
| `max_node_power_est_w` | Peak estimated node power | Peak estimated node power: Intel/AMD RAPL plus GPU on x86; on Grace, GH200 module power, else per-socket power summed over sockets plus GPU | Useful for peak power envelope checks and cooling stress. |
| `avg_node_power_est_w` | Mean estimated node power | Mean estimated node power | Energy-to-solution comparisons across runs/configurations. |
| `max_gpu_link_gbps` | Peak GPU PCIe and NVLink data rate | Peak GPU link bandwidth (PCIe/NVLink aggregate path) | Host-device/device-device transfer pressure indicator. |
| `max_gpu_clock_event_reasons` | Peak GPU clock throttling reasons | DCGM clock-event bitmask (the Metrics tab shows decoded flag names; Extended search filters on the numeric value) | Named flags (power cap, thermal, sync boost, ...) explain why clocks were limited; correlate with power/temp traces. |
| `mem_hwm` | Peak process resident memory (high water mark) | Prefers job-scoped cgroup memory (`host_mem_job`) when present; otherwise NUMA host memory (used - slab - file pages), in GiB | Compare with node RAM for host OOM risk<sup>[14](#ref-14)</sup>. On shared nodes this is the job's cgroup share, not whole-node RAM. |
| `node_imbalance` | CPU utilization imbalance across nodes | Node-level CPU rate imbalance | High values indicate decomposition/rank imbalance. |
| `time_imbalance` | CPU rate imbalance over job timeline | Temporal CPU imbalance across job timeline | Flags long underutilized windows or phase imbalance over time. |
| `flops_node_imbalance` | Floating-point rate imbalance across nodes | Node-level FLOP rate imbalance | Compute work unevenly distributed across nodes. |
| `fabric_node_imbalance` | Fabric bandwidth imbalance across nodes | Node-level fabric traffic imbalance | Some ranks/nodes communicate disproportionately. |
| `dram_bw_node_imbalance` | DRAM bandwidth imbalance across nodes | Node-level DRAM bandwidth imbalance | Memory pressure concentrated on subset of nodes. |
| `lnet_node_imbalance` | LNET bandwidth imbalance across nodes | Node-level LNet imbalance | Uneven filesystem/network load distribution. |
| `gpu_util_node_imbalance` | GPU utilization imbalance across nodes | Node-level GPU utilization imbalance | Multi-node training/inference skew across nodes. |
| `tensor_node_imbalance` | Tensor-pipe activity imbalance across nodes | Node-level tensor-activity imbalance | Tensor kernels unevenly distributed across participating nodes. |
| `vecpercent_64b` | Double-precision vector FLOP share (%) | Percent (0-100) of double-precision FLOPs done via vector widths > scalar | Low values on DP-heavy code suggest SIMD/vectorization opportunity<sup>[5](#ref-5)</sup>. |
| `avg_vector_width_64b` | Effective vector width (double precision) | Average effective DP vector width (the Metrics tab shows it with SP as one **Effective vector width (DP / SP)** row) | Closer to scalar indicates weak SIMD utilization in DP paths. |
| `vecpercent_32b` | Single-precision vector FLOP share (%) | Percent (0-100) of single-precision FLOPs done via vector widths > scalar | Low values on SP-heavy code suggest vectorization opportunity<sup>[5](#ref-5)</sup>. |
| `avg_vector_width_32b` | Effective vector width (single precision) | Average effective SP vector width (the Metrics tab shows it with DP as one **Effective vector width (DP / SP)** row) | Low average width indicates scalar/short-vector dominated SP execution. |

---

## 6. Data/plot surfaces and what they mean diagnostically

This section covers job-detail surfaces beyond scalar metrics.

### 6.1 Summary plots

- Diagnostic use: fastest phase/host outlier scan across CPU, memory, network, I/O, and GPU traces; when InfiniBand, Ethernet, or Omni-Path error counters have non-zero rates, the Summary grid adds one subplot per counter (each line is one host over time, with the same blue `?` help as other Summary metrics).
- Time axis and hover use the cluster timezone from site configuration (not raw UTC labels).
- GPU tensor traces: when IMMA/HMMA/DFMA splits are present, Summary shows separate tensor-pipe subplots for those kinds (preferred over a single lumped tensor-pipe series). The lumped any-pipe tensor series appears only when those splits are absent.
- Performance recommendation: always pair with Metrics tab; peaks in summary often explain extreme scalar maxima and telemetry behavior<sup>[12](#ref-12)</sup>.
- Unreadable samples: when the monitor could not read one of the counters a line adds up at some time, that point is left out instead of being drawn too low. A short gap inside a host's line is filled by a straight line between the samples on either side<sup>[17](#ref-17)</sup>, the same way the job metrics treat it; a gap at the start or end of the job, or a long one, stays empty. The same rule applies to roofline points and Type Detail plots. Peak values (for example the file-system peak columns) only use samples that were actually read.

### 6.2 Roofline tab (CPU + GPU)

- Diagnostic use: distinguish compute-ceiling vs bandwidth/link-ceiling regimes in the roofline model<sup>[1](#ref-1)</sup>.
- Recommendation: use `avg_flops`<sup>[3](#ref-3)</sup>, `avg_mbw`, `avg_gpu_mem_bw_gbps`, `max_gpu_link_gbps`, and fabric ratios to validate roofline reading.
- CPU vs GPU regime quick read:
  - **CPU roofline**: if points sit near the sloped bandwidth line, the phase is memory-bandwidth limited (data movement dominates); if points approach the flat top line, compute throughput dominates.
  - **GPU roofline**: each coral point is one **host, GPU device, and time** sample (not a sum of all GPUs on the node). The plot title shows which bandwidth axis was used - **GPU Roofline (Memory BW)** when estimated GPU memory bandwidth samples are present (same family of signal as Summary HBM BW), otherwise **GPU Roofline (PCIe/NvLink)** from interconnect byte rates. Apply the same compute vs bandwidth logic; low arithmetic intensity<sup>[2](#ref-2)</sup> phases are often HBM- or link-limited depending on that title, while high-intensity phases can become tensor/core compute limited. Device inventory is on the Resources panel; this plot is the per-device arithmetic intensity view.
  - **Interconnect context**: GPU scaling limits can reflect PCIe or NVLink behavior<sup>[7](#ref-7)</sup>, not just kernel math throughput - especially when the PCIe/NvLink title is shown.
  - **What to do with this**: memory/link-limited phases usually benefit from locality, data-layout, batching, or communication changes; compute-limited phases usually benefit from kernel efficiency, vector/tensor usage, and occupancy improvements<sup>[8](#ref-8)</sup>.
- Hovering the theoretical roof line shows **Peak performance (GFLOP/s)** and **Peak bandwidth (GB/s)** used to draw the curve (plain decimals), plus the axis units (**FLOP/byte**, **GFLOP/s**). Job-point hover includes host, device (or - when blank), AI, performance, and time. When one of CPU/GPU is still loading or unavailable, the ready chart appears first on the tab (same stacking on Multiprecision Mix).

### 6.3 Multiprecision Mix tab (CPU and GPU)

- Diagnostic use: quantify mixed-precision path composition (DP/SP/tensor, and CPU INT16/INT8 when present) as a share of **busy** arithmetic rates only (no idle wedge)<sup>[6](#ref-6)</sup>.
- CPU pie: the share of floating-point and vector-integer instructions by precision: FP64 / FP32 / FP16 (`avg_fp64_instr` / `avg_fp32_instr` / `avg_fp16_instr`) plus INT16 / INT8 (`avg_arm_int16_ops` / `avg_arm_int8_ops`). On Grace and Intel nodes it counts instructions, not FLOPs (one vector instruction counts once), so it shows which precisions the code uses, not how much arithmetic each one did. On AMD Zen5 nodes the CPU only counts FLOPs by precision, so the FP64 / FP32 wedges there are FLOP shares. AMD Zen3 and Zen4 CPUs (for example Lonestar6 Milan nodes) have no precision split at all, so those jobs show no CPU pie ("not available"); use `avg_flops` for their FLOP rate.
- When the CPU or GPU counters were collected but every rate is 0 (an idle job), the pie says the telemetry is present but idle, which is different from counters that were never collected.
- GPU pie: each wedge is a pipe's **busy share** (how much of the time that pipe was working), not its share of FLOPs; a pipe that is busy half the time can still do far fewer operations than another. Hover shows share of busy percent. Wedges, when positive: **Tensor IMMA (INT8/INT4)**, **Tensor HMMA (FP16/BF16/TF32/FP8)**, **Tensor DMMA (FP64)** (Hopper and Blackwell only), **Tensor (unattributed)**, **FP16**, **FP32**, **FP64** and **Integer pipe**.
  - **Tensor (unattributed)** is the tensor-core activity that no split explains: total tensor activity minus HMMA, IMMA and DMMA, shown only when splits exist and the remainder is more than a tiny amount. When no split is reported the whole tensor activity is one **Tensor** wedge instead.
  - HMMA is one counter for FP16, BF16, TF32 and FP8 tensor math, so the pie cannot tell those precisions apart and any tensor FLOP figure derived from it is an estimate.
  - There is no DFMA wedge: DFMA is the FP64 pipe itself (it equals FP64 on Hopper and Blackwell), so a wedge for it would count FP64 work twice. FP64 work appears as **FP64** (pipe) and **Tensor DMMA (FP64)** (tensor cores).
  - **Integer pipe** is busy under floating-point work on Hopper (address and index math, about half the time) and near 0 on Blackwell, so compare its wedge only between jobs on the same GPU generation.
- GPU FLOP figures on Blackwell: GB200 (Horizon) and RTX PRO 6000 (Stampede3 amd-rtx) nodes count tensor work in the GPU FLOP rates (the GPU Roofline points), using tensor peaks measured on the clusters (GB200: FP16 / BF16 and FP64 tensor work; RTX PRO 6000: FP16 / BF16 only, since its FP64 matrix math does not use tensor cores).
- Width behavior: each pie includes the precision widths that have usable positive metrics for that job/architecture; missing widths are omitted rather than treated as errors.
- Recommendation: when model/code changes precision policy, compare this tab first, then check throughput/utilization deltas.

### 6.4 Resources panel (FSIO + GPU summary + logs)

- Diagnostic use: rapid verification of I/O volume, burst peaks, and GPU occupancy before deep plotting. Lustre, NFS, and BeeGFS resource rows can all appear when each client has data for the job.
- Recommendation: if FS totals or **Peak MB/s** / **Peak IOPS** are high, check `avg_sharedfs_bw`, `avg_sharedfs_iops`, `max_mds`, and `max_lnetbw` for bottleneck type; this is usually an I/O bottleneck triage path<sup>[11](#ref-11)</sup>. GPU statistics sit above the log buttons so you scan filesystem and accelerator context together before opening logs.

### 6.5 Execution and hosts tab

- Diagnostic use: detect environment drift (wrong module/library/container path) and host-specific anomalies.
- Recommendation: for regressions with "same script," verify this tab before tuning code.

### 6.6 Device data tab

- Diagnostic use: confirms which counter families/events were actually collected for this job.
- Recommendation: use this tab to confirm expected telemetry families when interpreting plots/metrics.

---

## 7. Failure-mode checklist

Metric names below are the keys from the section 5 catalog; the Metrics tab shows each one under its short label.

### 7.1 Job did not run intended workload

- Signals: short runtime, flat/empty summary traces, empty process list.
- Check: `Execution and hosts` (exec path/cwd/libset), `Processes`, scheduler record.

### 7.2 Timeout/preemption risk

- Signals: runtime near the time limit you requested, abrupt trace cutoff.
- Check: tail-end spikes in FS/fabric/GPU activity; adjust timelimit or checkpoint cadence<sup>[13](#ref-13)</sup>.

### 7.3 Host memory pressure / OOM risk

- Signals: high `mem_hwm`, elevated `max_numa_remote_rate`, imbalance in `dram_bw_node_imbalance`.
- Check: first-touch policy, rank/thread placement, per-rank memory growth.

### 7.4 GPU memory or accelerator under-use

- Signals: low `avg_gpuutil`, low `avg_tensor_active`, low `detail_gpu_util_mean` with full `detail_gpu_count`.
- Check: input staging bottlenecks, wrong device mapping, too-small kernels, launch configuration.

### 7.5 Communication-dominated scaling

- Signals: high `avg_ibbw`, `max_fabricbw`, `avg_fabric_mb_per_gflops`, `avg_fabric_mb_per_avg_tensor`, `max_packetrate` peaks<sup>[15](#ref-15)</sup>.
- Check: decomposition, collective strategy, message size (`avg_packetsize`), rank mapping.

### 7.6 Filesystem/metadata bottlenecks

- Signals: high `avg_sharedfs_iops`, `max_mds`, `max_lnetbw`, large Shared File Systems totals on Resources.
- Check: file-per-rank patterns, small sync writes, metadata-heavy loops.

### 7.7 Throttling/power constraints

- Signals: named flags in `max_gpu_clock_event_reasons`, high `max_gpu_power`, capped `max_node_power_est_w` with performance dips.
- Check: power caps, thermal conditions, cluster policy limits.

### 7.8 Cross-node imbalance / stragglers

- Signals: elevated `node_imbalance`, `flops_node_imbalance`, `gpu_util_node_imbalance`, `tensor_node_imbalance`, `fabric_node_imbalance`, `lnet_node_imbalance`, `dram_bw_node_imbalance`.
- Check: domain decomposition, skewed rank placement, node health differences.

### 7.9 Data continuity checks

- Signals: gaps in expected plot/metric continuity.
- Check: `Device data` event coverage for expected signal families and compare with similar jobs.
- Check: a **Metrics** row reading "collector not found" or "counter not present (n/N hosts)" means that signal does not exist on those nodes; it is not a gap in your job.
- Check: a straight segment over a few samples in a Summary or Type Detail line is a short read gap filled between its neighbors, not a measured plateau; if a plot is missing for one signal family, the gaps were too long or at the edge of the job to fill.

---

## 8. Appendix - concept guide with external references

Use these numbered references when you want background on terms used throughout sections 1-7.

| Ref | Concept | Why it matters for this guide | Reference |
| --- | ------- | ----------------------------- | --------- |
| <span id="ref-1">[1]</span> | Roofline model | Explains the core "bandwidth-limited vs compute-limited" interpretation used in the Roofline tab. | [Roofline model (Wikipedia)](https://en.wikipedia.org/wiki/Roofline_model) |
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
| <span id="ref-17">[17]</span> | Linear interpolation | How short read gaps in plot lines are filled from the samples on either side. | [Linear interpolation (Wikipedia)](https://en.wikipedia.org/wiki/Linear_interpolation) |

---

## 9. Related references in this repository

- **Counter and variable catalog:** `docs/MONITOR_VARIABLES.md`
