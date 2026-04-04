# Using HPCPerfStats on the Web — Guide for Researchers and HPC Users


| Field            | Value                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------- |
| **Audience**     | People who run jobs on the cluster and want to interpret site data                          |
| **Scope**        | Django + React job views under `/machine/` (job lists, job detail, type detail, host plots) |
| **Last updated** | 2026-04-03                                                                                  |


This document is ordered so the **most decision-relevant ideas come first**. Deeper catalog-style detail appears in later sections. Telemetry is collected by a node monitor and joined with scheduler accounting; the pipeline is **near-line**, so very recent jobs may show empty plots or “metric not computed” until background processing finishes.

---

## 1. What you should do first on a job page

1. **Read scheduler metadata** (status, runtime vs requested time, cores, nodes, queue). This frames everything else: a failed or very short job may have little telemetry.
2. **Scan the summary plot** (if present). It is the fastest way to see whether the run was CPU-bound, memory-bandwidth-heavy, GPU-active, doing heavy I/O or network, and whether behavior **changed over time** or **differs by node** (one line per host).
3. **Check the heatmap** when available. It encodes **cycles per instruction (CPI)** per host over time—useful for spotting **stragglers**, **phases** (e.g. initialization vs steady state), and **load imbalance** across nodes.
4. **Use roofline plots** when you care about **how close the code gets to peak compute vs peak memory traffic** (CPU roofline) or **GPU compute vs PCIe/NVLink byte traffic** (GPU roofline, when counters exist). They support “are we leaving performance on the table?” conversations.
5. **Open the job-level metrics table** for single-number summaries (averages, peaks, imbalance percentages). Many rows have a **help icon** (variable metadata) next to the name—use it for definitions.
6. **Drill into “Device Data and Plots”** when you need raw counter families (`host_data.type` names) or per-type time series on the type-detail page.

If a panel says data is unavailable, the message is often literal (counter not enabled, wrong hardware path, or no overlapping samples)—see §8.

---

## 2. Finding jobs and reading the job list

- **Search home**: Browse by **year** or **date** to reach filtered job lists.
- **Job list table**: Typical columns include job ID, submit/start/end times, **runtime**, **requested time (timelimit)**, resource shape (**nodes**, **cores**), **user**, **project/account**, **queue**, **state**, and **job name**. Row **background color** reflects completion state (e.g. completed vs failed vs other).
- **Histograms** (where configured): Distribution thumbnails for metrics such as **runtime**, **node count**, and **queue wait** help you see whether your job is typical for that filter.
- **Performance Data** column: Short status labels (e.g. summary available, monitoring gaps, not summarized yet) reflect whether usable metric values exist, sample coverage, and pipeline state—a gray “not summarized” row does not always mean a bad run; it can mean ingest or metric computation has not finished, or the job had no monitor coverage.

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


**Staff-only:** “Sample count” reflects how many distinct timestamps were seen for metrics on the job—operators use it to diagnose sparse or broken sampling.

---

## 4. Host-level plots (Bokeh)

All plots support **per-host coloring** where multiple nodes ran the job. Use **zoom** (link on each panel) for a full-screen view.

### 4.1 Summary plot

A **grid of step plots over local time**, one subplot per available metric family. Typical groupings (when data exists):

- **CPU usage** — user/system time proxy: is the CPU actually busy?
- **Memory / NUMA** — footprint and **remote NUMA references** when exposed: **NUMA misplacement** hurts performance.
- **DRAM / socket bandwidth** — Intel IMC- or AMD DF-derived rates: **memory bandwidth limits** for stencil / sparse / streaming codes.
- **Instruction throughput / FLOPs / cycles** — computational intensity over time; **Intel CHA**-derived traces (when present) give a coarse **uncore / LLC / coherence pressure** signal for multi-threaded or MPI+OpenMP codes.
- **Frequency / package power** — **thermal throttling**, power caps, or **turbo** behavior.
- **GPU** — utilization, framebuffer usage, **tensor core / SM / FP pipe** activity, estimated **HBM bandwidth**, **power**, **PCIe/NVLink byte rates** (vendor-dependent).
- **Lustre / NFS client** — read/write throughput and metadata-heavy **IOPS**: **I/O storms**, small-file metadata bottlenecks.
- **Network / fabric** — InfiniBand (or related) byte rates, **fabric bytes per GFLOP** and **fabric bytes per average tensor activity** (heuristic **communication intensity** for HPC and GPU+MPI workflows).
- **Node power estimate** — Combined view when CPU/GPU/module power fragments exist.

**How to read it:** Look for **flat lines vs spikes**, **one node an outlier**, and **correlation** (e.g. GPU util drops while network rises → **halo exchange** or **checkpoint**).

### 4.2 Heatmap (CPI)

**Color = CPI** (cycles per instruction) by **host (rows)** and **time (columns)**—lower is often “more efficient instruction mix / fewer stalls,” but **very low CPI with low absolute performance** can still mean **not much useful work**. Sudden **vertical stripes** (all nodes) indicate **phases**; **horizontal outliers** indicate **straggler nodes**.

### 4.3 CPU roofline

Scatter of **arithmetic intensity (FLOP/byte)** vs **achieved GFLOP/s**, with a **roofline** curve from nominal peak FLOPs and DRAM bandwidth inferred from the job’s counter schema (**Intel**, **AMD**, **ARM Grace-class** paths each have rules). **Points near the memory roof** → bandwidth-limited; **below both roofs** → latency, serial sections, or **low vectorization**.

### 4.4 GPU roofline (PCIe / NVLink)

Uses **NVIDIA** `nvidia_gpu` counters when present: **GPU FLOP/s** vs **PCIe + NVLink byte bandwidth** derived from **profiler-style link byte counters**—not framebuffer “GPU mem copy” alone. **High AI with low link GB/s** suggests the kernel is not **host–device transfer bound** on that axis; **points hugging the link roof** suggest **streaming / staging** limits. **Missing plot** usually means strict counters were not available or did not overlap in time.

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


| Panel                           | Content                                                     | Diagnostic use                                                                                                                                              |
| ------------------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Shared file system**          | Per-filesystem **MB read / written** (job totals)           | **Checkpoint / post-processing** I/O; compare with Lustre/NFS summary subplots for **time structure**.                                                      |
| **Client / server logs**        | External log URLs (when configured)                         | Jump to application or system logs; timestamps should align with job window.                                                                                |
| **GPU statistics**              | Allocated vs **active** GPU count, **max/mean utilization** | **Under-used GPUs**: wrong binding, **serialization**, or **CPU bottleneck**.                                                                               |
| **Processes**                   | Observed process command lines (when collected)             | Verify **which binary** actually ran (wrapper scripts, **wrong conda env**, **srun** depth).                                                                |
| **Job-level metrics**           | Full catalog of scalar metrics (see §7–§9)                  | Single-place **bottleneck and imbalance** summary.                                                                                                          |
| **Execution parameters (XALT)** | Executable path, cwd, **loaded shared libraries**           | **Environment drift** between interactive and batch; **wrong library** (e.g. CPU BLAS vs GPU). Only populated when XALT integration is enabled server-side. |
| **Hosts**                       | Nodelist                                                    | Tie plots to specific nodes; compare with scheduler nodelist if troubleshooting **stragglers**.                                                             |


---

## 7. Theme: HPC-relevant variables and metrics

This theme covers **traditional parallel / scientific computing** signals. Names match the **job-level metrics** list and **summary** subplots where applicable.

### 7.1 CPU and instruction efficiency

- `**avg_cpuusage` (#cores)**: Time-averaged **user/system** load vs allocation. Persistently **below expected parallel efficiency** → synchronization, I/O wait, or **OpenMP/MPI under-subscription**.
- **Heatmap CPI**: **Stragglers** and **phases**; rising CPI can mean **memory stalls** or **more complex instruction mix** (e.g. after a **JIT** or **adaptive** phase).
- `**avg_freq` (GHz)**: **Frequency behavior** vs power/thermal policy; compare with **package power** traces on the summary plot.
- `**vecpercent_*`, `avg_vector_width_*`**: **SIMD width mix** (Intel FP arithmetic counters). Low vector % for FP-heavy loops → **compiler flags**, **data layout**, or **mixed precision** not using wide vectors.

### 7.2 Memory, NUMA, and DRAM bandwidth

- **Summary `mem` / NUMA / `mbw` / `amd_mbw`**: **Footprint**, **remote NUMA traffic**, and **socket DRAM bandwidth**. High **remote refs** → **numactl**, first-touch, or **MPI rank placement** fixes.
- `**mem_hwm` (GiB)**: **High-water RSS**-style signal (when telemetry supports it). Compare to **per-node RAM** to assess **OOM risk** on larger inputs.
- `**dram_bw_node_imbalance` (%)**: Spread of **DRAM bandwidth** across nodes—**different subdomain sizes**, **imbalanced decomposition**, or **one rank hoarding bandwidth**.

### 7.3 Parallel imbalance (CPU-side)

- `**node_imbalance` / `time_imbalance` (%)**: CPU usage **uneven across nodes** or **over time** within the job window—classic **load imbalance** or **root process** doing extra work.
- `**flops_node_imbalance` (%)**: When FLOPs counters exist, quantifies **compute imbalance** across nodes (not just utilization).

### 7.4 Interconnect and I/O

- `**avg_ibbw`, `max_fabricbw`, `max_packetrate`, `avg_packetsize`**: **InfiniBand / related fabric** usage and **message size** hints—**many small messages** vs **bulk transfers**.
- `**fabric_node_imbalance` (%)**: **MPI traffic** unevenly distributed across nodes (sometimes **rank-to-node mapping** or **I/O rank** effects).
- `**max_lnetbw`, `lnet_node_imbalance`**: **Lustre network (LNet)** throughput and imbalance—**read-heavy** vs **write-heavy** parallel I/O patterns.
- `**avg_sharedfs_bw`, `avg_sharedfs_iops`, `max_mds`**: **Client-side** shared filesystem **bandwidth** and **metadata / ops** rates—**small files**, **directory creation storms**, **ls -R** in batch scripts.
- `**avg_blockbw`**: **Local block device** traffic—**scratch** spill, **local checkpoint**, or **unexpected disk** use.

### 7.5 Power and reliability-ish signals

- `**max_node_power_est_w`, `avg_node_power_est_w`**: **Blended node power** when CPU/GPU/module fragments allow—**energy-to-solution** comparisons across algorithm choices.
- `**max_opa_congestion_rate`**: **Intel OPA**-specific congestion signaling when present—**network quality** issues vs pure bytes.

### 7.6 Ethernet (when relevant)

- `**avg_ethbw`**: **TCP/IP** heavy jobs (object storage, HTTP, **distributed DL** over RoCE-free paths)—complements IB-centric metrics.

---

## 8. Theme: AI, GPUs, and large-model workflows

### 8.1 Utilization and “is the accelerator working?”

- `**avg_gpuutil` (%)** and summary **GPU util**: Distinguish **idle GPUs** (launcher bugs, **CPU preprocessing** bottleneck) from **sustained work**.
- `**gpu_util_node_imbalance` (%)**: Multi-node training **straggler GPUs** or **uneven batch** distribution.

### 8.2 Kernels: tensor cores, occupancy, precision

- **Summary: `tensor_active`, `sm_occupancy`, `fp16_active`, `fp32_active`**: Whether time is in **tensor-heavy** paths, **occupancy-limited** kernels, or **FP32-dominated** regions—useful when **changing precision** or **framework version**.
- `**avg_tensor_active` (%)** and `**tensor_node_imbalance` (%)`**: Job-level **tensor pipeline** use and **cross-node evenness**.

### 8.3 Memory system on the GPU

- **Framebuffer usage / `mem_util`**: **OOM** risk on GPU; **fragmentation** patterns when combined with time.
- `**avg_gpu_mem_bw_gbps`**: **HBM bandwidth** utilization (when exposed)—**memory-bound** layers vs **compute-bound** layers in deep networks.

### 8.4 Power, clocks, and throttling

- `**max_gpu_power` (W)**: **Power headroom** and **cooling** stress.
- `**max_gpu_clock_event_reasons`**: Opaque bitmask from **clock throttle reasons**—nonzero values warrant correlating with **temperature**, **power cap**, or **workload burstiness** (vendor documentation applies).

### 8.5 Multi-GPU and host–device data movement

- `**max_gpu_link_gbps`**: **PCIe/NVLink byte traffic** peaks—**staging**, **D2D**, **collectives** that use GPU-direct paths.
- **GPU roofline**: Relates **achieved FLOP/s** to **link GB/s** for **NVIDIA** when strict counters exist—complements CPU roofline for **heterogeneous** steps.
- `**fabric_mb_per_gflops` / `fabric_mb_per_avg_tensor` (summary)** and `**avg_fabric_mb_per_avg_tensor` (metric)**: **Communication intensity** relative to **compute** or **tensor activity**—**weak scaling** studies and **MPI+GPU** jobs where you expect **rising comms** with scale.

### 8.6 CPU-side companions

- `**dram_bw_node_imbalance`**, **CPU roofline**, and **CHA**-related summary traces: **Data loader**, **augmentation**, or **checkpoint** phases often show on **CPU memory / uncore** before **GPU util** rises.

---

## 9. Theme: failure modes and diagnostic reasoning (extended)

Use this section as a **checklist** when jobs **fail**, **underperform**, or **behave differently** across runs. Pair each hypothesis with **specific** site signals.

### 9.1 Job never really “ran” your science

- **Symptoms:** Very short **runtime**, **zero or flat** CPU/GPU plots, empty **processes**.
- **Checks:** **Executable path / cwd / libraries (XALT)** vs what you intended; **process list** for wrapper vs real binary; **start/end** times vs your script’s `sleep` or **immediate exit**.

### 9.2 Time limit and preemption

- **Symptoms:** **State** failed or incomplete; **runtime ≈ timelimit**; plots **cut off** sharply at end.
- **Checks:** Summary **I/O and network** spikes near the end (**checkpoint** rush); **GPU or CPU** still busy → need **more time** or **better checkpoint interval**.

### 9.3 Out-of-memory (host)

- **Symptoms:** Scheduler **OOM** or **SIGKILL**; **mem** subplot **near machine limits**; `**mem_hwm`** high relative to node RAM.
- **Checks:** **NUMA remote** traffic spikes (allocation on wrong socket); **nhosts × per-rank memory** miscalculation.

### 9.4 Out-of-memory (GPU)

- **Symptoms:** Job abort with CUDA/HIP OOM messages (in logs); **GPU framebuffer** trace **near capacity** before failure.
- **Checks:** **Batch size**, **activation checkpointing**, **gradient accumulation**; multi-GPU **uneven** memory if **tensor parallel** mapping is wrong.

### 9.5 CPU-bound / low parallel efficiency

- **Symptoms:** `**avg_cpuusage`** low relative to cores; **CPI heatmap** “cold” regions; **CPU roofline** points **well inside** the roof.
- **Checks:** **OpenMP threads**, **MPI rank count**, **binding** (`taskset`, `srun --cpu-bind`); **I/O or network** waits visible on summary; **vector** metrics show **scalar-heavy** code.

### 9.6 Memory-bandwidth-bound (CPU)

- **Symptoms:** **CPU roofline** near **DRAM roof**; high `**mbw` / `amd_mbw`** in summary; `**dram_bw_node_imbalance**` low but absolute BW high.
- **Checks:** **Data layout** (AoS vs SoA), **spatial order** of stencils, **thread count** vs memory channels.

### 9.7 NUMA and locality

- **Symptoms:** High `**numa_remote_refs`** (when present); **node_imbalance** without obvious MPI cause; performance **varies by rerun** on same queue.
- **Checks:** **First-touch** allocation policy; **same executable** on different **nodelist** sizes.

### 9.8 I/O-bound and metadata storms

- **Symptoms:** `**avg_sharedfs_iops`**, `**max_mds**`, or Lustre/NFS summary traces **dominate**; **CPU** partially idle while **llite** busy.
- **Checks:** **File-per-rank** patterns, **small writes**, **directory listing** in job scripts; move to **shared files**, **burst buffers**, or **fewer, larger** operations.

### 9.9 Network-bound (MPI / collectives)

- **Symptoms:** High `**avg_ibbw` / `max_fabricbw`** correlated with **low CPU FLOPs**; `**fabric_node_imbalance`** or `**lnet_node_imbalance**`; `**opa_wait_cong` / `opa_ecn**` elevated on OPA.
- **Checks:** **Process grid** vs **torus** / **fat-tree**; **collective algorithms**; **message size** (`avg_packetsize`).

### 9.10 GPU under-utilization

- **Symptoms:** **Low `avg_gpuutil`**, low **tensor / FP** activity; **GPU roofline** not applicable or points **very low**.
- **Checks:** **Data loader** on CPU (watch **CPU memory BW** and **GPU util** time shift); **too-small kernels**; **Python overhead**; **wrong device** in framework.

### 9.11 Host–device transfer or link saturation

- **Symptoms:** High **link GB/s** on summary; **GPU roofline** near **link roof**; `**max_gpu_link_gbps`** high with moderate FLOPs.
- **Checks:** **Pinned memory**, **cudaHostRegister**, **NCCL** settings, **PCIe** slot / **NVLink** topology.

### 9.12 Thermal or power throttling

- **Symptoms:** `**max_gpu_clock_event_reasons`** nonzero; **frequency** or **power** traces **dip** under sustained load; **node power** **flat-caps** at a ceiling.
- **Checks:** **Datacenter policy**, **GPU power cap**, **fan / cooling** events (with system logs).

### 9.13 Load imbalance and stragglers

- **Symptoms:** **Heatmap** shows **one or few hot rows** (high CPI or very different color); `**node_imbalance`, `flops_node_imbalance`, `gpu_util_node_imbalance`, `tensor_node_imbalance`** elevated; summary plots show **one host line** far from others.
- **Checks:** **Domain decomposition**, **dynamic load balance**, **I/O rank** or **checkpoint** on subset; **broken node** (compare **host name**).

### 9.14 “Wrong build” or dependency drift

- **Symptoms:** Performance regression with **same script**; **XALT libraries** show unexpected **MPI** or **BLAS** paths.
- **Checks:** **Module** versions; **RPATH**; containers **digest** vs tag.

### 9.15 Missing data on the website (operational)

- **“Data not available” / empty plot:** Monitor not running on those nodes, **counter not enabled** in monitor build, job **outside** retained archive, or metrics job **not yet computed**.
- **Staff** may see `**no_data_reason`** text for a metric; non-staff often see a generic **not available** message for empty values.
- **Partial plots:** Progressive loading may fill in later—**refresh** after a short wait.

---

## 10. Related references in this repository

- **System architecture and data flow:** `docs/design-document.md`
- **Counter types and CPU/GPU analysis contracts:** `hpcperfstats/analysis/README_ARCH_AGNOSTIC.md`
- **Exhaustive monitor variable catalog (operator-oriented):** `docs/MONITOR_VARIABLES.md`

---

## Document history


| Date       | Change                                                                                  |
| ---------- | --------------------------------------------------------------------------------------- |
| 2026-04-03 | Initial researcher-facing guide aligned with current job detail UI and metrics catalog. |


