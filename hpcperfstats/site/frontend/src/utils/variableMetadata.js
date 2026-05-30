/**
 * Tooltip copy keyed by metric / event short name (before units in brackets).
 *
 * Merge order: monitor `host_data.event` names (see variableMetadataMonitorEvents.js),
 * then job accounting / derived metrics / API-only fields (this file). Later entries win
 * on purpose so UI-specific wording overrides generic monitor text.
 *
 * Optional `researcherUse`: how researchers reason about HPC, AI, or failure diagnosis
 * (aligned with docs/using-the-website-as-a-researcher.md). Shown below the definition
 * in the job-detail help tooltip with a separator.
 */

import { MONITOR_EVENT_METADATA } from "./variableMetadataMonitorEvents.js";

const JOB_ACCOUNTING_AND_DERIVED_METADATA = {
  // ===== Job accounting fields (job_data model) =====
  jid: {
    description: "Slurm job ID (unique identifier for the job).",
    researcherUse: "Cross-reference logs, support tickets, and reproducer scripts.",
  },
  username: {
    description: "Username of the job owner from Slurm accounting.",
    researcherUse: "Ownership and fair-share context.",
  },
  host: {
    description: "Compute node hostname recorded in the job allocation host list.",
    researcherUse:
      "Find jobs that ran on a specific node when investigating node-local failures or performance anomalies.",
  },
  account: {
    description: "Project/account charged for the job in Slurm accounting.",
    researcherUse: "Billing and policy: which allocation was charged.",
  },
  start_time: {
    description: "Timestamp when the job started running.",
    researcherUse: "Align with external logs; detect an unexpected early end.",
  },
  end_time: {
    description: "Timestamp when the job finished running.",
    researcherUse: "Align with external logs; detect an unexpected early end.",
  },
  runtime: {
    description: "Elapsed runtime of the job in seconds.",
    researcherUse:
      "Compare to requested time: jobs that always use much less than requested may be over-requesting; jobs near the limit may be timing out.",
  },
  timelimit: {
    description: "Requested walltime limit for the job in seconds.",
    researcherUse: "Risk of preemption or timeout if runtime approaches this value.",
  },
  queue: {
    description: "Queue/partition the job ran in.",
    researcherUse: "Explains hardware policy (GPU versus CPU, debug versus production).",
  },
  jobname: {
    description: "Job name as submitted to the scheduler.",
    researcherUse: "Often encodes an experiment id or binary name for a quick sanity check.",
  },
  state: {
    description: "Final scheduler state for the job (e.g., COMPLETED, FAILED).",
    researcherUse: "Failed jobs may still have partial telemetry—still worth opening the job page.",
  },
  ncores: {
    description: "Number of CPU cores allocated to the job.",
    researcherUse: "Match to how you launched MPI or OpenMP; miscounts can mean wrong binding.",
  },
  nhosts: {
    description: "Number of nodes allocated to the job.",
    researcherUse:
      "Multi-node imbalance metrics and network plots only make sense in this node-count context.",
  },
  node_hrs: {
    description: "Allocated node-hours for the job: node count multiplied by elapsed runtime in hours.",
    researcherUse:
      "Estimate allocation cost and compare resource scale across jobs with different runtimes or node counts.",
  },
  metrics_distinct_time_count: {
    description:
      "Sum over job hosts of distinct sample timestamps in host_data for this job’s time window, as recorded when job-level metrics were last persisted. This is not the raw row count of host_data.",
    researcherUse: "Operators use sample count to diagnose sparse or broken sampling.",
  },

  // ===== Job-level derived metrics (hpcperfstats/analysis/metrics/metrics.py catalog) =====
  avg_blockbw: {
    description:
      "Average block-device throughput computed from read and write sectors over the job (GB/s).",
    researcherUse:
      "Local scratch spill, local checkpoint, or unexpected disk traffic versus shared filesystem work.",
  },
  avg_cpuusage: {
    description:
      "Average number of CPU cores busy, inferred from cumulative user/system/nice CPU time deltas over the job window.",
    researcherUse:
      "Persistently below expected parallel efficiency can mean synchronization, I/O wait, or OpenMP/MPI under-subscription; pairs with CPU-bound failure checks.",
  },
  avg_sharedfs_iops: {
    description:
      "Average metadata and I/O operation rate for the shared filesystem named in the Shared File System section on this page (Lustre llite and/or NFS READ/WRITE operation counters over the job).",
    researcherUse:
      "Metadata-heavy jobs: small files, directory creation storms, or recursive listing in batch scripts.",
  },
  avg_sharedfs_bw: {
    description:
      "Average read+write bandwidth for the shared filesystem named in the Shared File System section on this page (Lustre llite and/or NFS byte counters over the job).",
    researcherUse:
      "Checkpoint and post-processing I/O; compare with Lustre/NFS summary subplots for time structure.",
  },
  avg_ibbw: {
    description:
      "Average high-speed fabric bandwidth from InfiniBand extended counters, else Omni-Path, else summed Ethernet bytes (MB/s).",
    researcherUse:
      "Fabric usage and message-size hints; high fabric with low CPU FLOPs can suggest network-bound MPI or collectives.",
  },
  avg_fabric_mb_per_gflops: {
    description:
      "Ratio of average fabric bandwidth (MB/s) to average floating-point throughput (GFLOP/s), using the same job-mean fabric and FLOP paths as avg_ibbw and avg_flops.",
    researcherUse:
      "Weak scaling and MPI+GPU studies where communication intensity should rise with scale.",
  },
  avg_tensor_active: {
    description:
      "Average DCGM tensor-pipe activity percentage (NVIDIA preferred; AMD GPU when the same field is populated).",
    researcherUse:
      "See whether time sits in tensor-heavy paths when changing precision or framework version.",
  },
  avg_fp16_active: {
    description:
      "Average GPU FP16 pipeline activity percentage (NVIDIA preferred; AMD GPU when the same field is populated).",
    researcherUse:
      "Quantifies lower-precision execution share for mixed-precision training and inference runs.",
  },
  avg_fp32_active: {
    description:
      "Average GPU FP32 pipeline activity percentage (NVIDIA preferred; AMD GPU when the same field is populated).",
    researcherUse:
      "Highlights single-precision dominated phases when comparing precision policies.",
  },
  avg_fp64_active: {
    description:
      "Average GPU FP64 pipeline activity percentage (NVIDIA preferred; AMD GPU when the same field is populated).",
    researcherUse:
      "Shows double-precision intensity for numerically sensitive kernels on GPUs.",
  },
  avg_gpu_mem_bw_gbps: {
    description:
      "Average estimated GPU HBM/memory bandwidth rate from DCGM (GB/s), job-mean across hosts and time buckets.",
    researcherUse:
      "Memory-bound versus compute-bound layers in deep networks when HBM counters exist.",
  },
  avg_fabric_mb_per_avg_tensor: {
    description:
      "Heuristic ratio: average fabric MB/s divided by average tensor-activity percent (scaled to a fractional duty cycle), for coupled MPI plus GPU workloads.",
    researcherUse:
      "Communication intensity relative to tensor activity for heterogeneous MPI+GPU workflows.",
  },
  avg_flops: {
    description:
      "Average floating-point throughput (GFLOP/s) from AMD FLOPS PMC, Intel FP_ARITH or legacy SSE proxies, or ARM synthetic counters.",
    researcherUse:
      "Computational intensity over time; pair with fabric bytes and CPU roofline for bottleneck stories.",
  },
  avg_mbw: {
    description:
      "Average DRAM memory bandwidth (GB/s) from AMD Data Fabric channels, Intel IMC CAS counters, ARM IMC, or synthetic ARM/DCGM bytes.",
    researcherUse:
      "High DRAM bandwidth with CPU roofline near the memory roof points to bandwidth-limited CPU kernels.",
  },

  avg_freq: {
    description:
      "Average effective CPU frequency (GHz) from APERF/MPERF or equivalent PMC ratios over the job.",
    researcherUse:
      "Frequency versus power and thermal policy; compare with package power traces on the summary plot.",
  },
  avg_ethbw: {
    description:
      "Average Ethernet bandwidth computed from per-interface receive+transmit byte counters over the job (MB/s).",
    researcherUse:
      "TCP/IP-heavy paths: object storage, HTTP, or distributed deep learning without RoCE.",
  },
  detail_gpu_active: {
    description:
      "Count of GPU devices with utilization above zero in the job window (from SQL aggregates on nvidia_gpu gpu_util / utilization).",
    researcherUse:
      "Quick read on how many devices showed non-idle utilization during the job.",
  },
  detail_gpu_util_max: {
    description:
      "Sum of per-device peak GPU utilization (%) in the job window (same aggregate as job detail).",
    researcherUse:
      "Upper-bound style signal when comparing against device counts; not an average across devices.",
  },
  detail_gpu_util_mean: {
    description:
      "Sum of per-device average GPU utilization (%) in the job window (same aggregate as job detail).",
    researcherUse:
      "Matches the job-detail GPU mean % and the catalog metric GPU % (avg_gpuutil).",
  },
  detail_gpu_count: {
    description:
      "Total GPU count for the job from gpu_count telemetry (nvidia_gpu preferred, else amd_gpu).",
    researcherUse:
      "Hardware capacity context for utilization and activity counts.",
  },
  detail_fsio_llite_read_mb: {
    description:
      "Total Lustre llite read_bytes delta over the job window (MB), for job-detail FSIO (same path as live host_data when metrics are missing).",
    researcherUse:
      "Parallel file read volume on Lustre when llite telemetry is present.",
  },
  detail_fsio_llite_write_mb: {
    description:
      "Total Lustre llite write_bytes delta over the job window (MB), for job-detail FSIO.",
    researcherUse:
      "Parallel file write volume on Lustre when llite telemetry is present.",
  },
  detail_fsio_nfs_read_mb: {
    description:
      "Total NFS client read byte deltas (aggregated counter families) over the job window (MB), used when Lustre llite is absent.",
    researcherUse:
      "NFS-heavy workflows when Lustre llite is not available.",
  },
  detail_fsio_nfs_write_mb: {
    description:
      "Total NFS client write byte deltas over the job window (MB), used when Lustre llite is absent.",
    researcherUse:
      "NFS-heavy workflows when Lustre llite is not available.",
  },
  detail_fsio_llite_peak_mb_s: {
    description:
      "Peak aggregate Lustre client MB/s over the job: maximum over sample times of (read MB/s + write MB/s), each summed across hosts, using the same llite byte counters as the summary plot.",
    researcherUse:
      "Short burst read/write load on Lustre versus long-window totals.",
  },
  detail_fsio_llite_peak_iops: {
    description:
      "Peak aggregate Lustre client metadata IOPS: maximum over sample times of summed llite arc rates for the same metadata event set as the summary liops panel.",
    researcherUse:
      "Burst metadata load (creates, stats, readdirs) versus steady streaming.",
  },
  detail_fsio_nfs_peak_mb_s: {
    description:
      "Peak aggregate NFS client MB/s over the job: maximum over sample times of summed read and write MB/s (same counter families as summary NFS read/write panels).",
    researcherUse:
      "Short NFS throughput bursts when Lustre llite is absent.",
  },
  detail_fsio_nfs_peak_iops: {
    description:
      "Peak aggregate NFS client IOPS: maximum over sample times of summed READ_ops and WRITE_ops arc rates.",
    researcherUse:
      "Burst small-file or metadata-heavy NFS phases.",
  },
  avg_gpuutil: {
    description:
      "GPU utilization % for the job: same value as detail GPU mean % (SQL aggregate over per-device averages, not a trimmed time-series mean).",
    researcherUse:
      "Distinguish idle GPUs (launcher bugs, CPU preprocessing bottleneck) from sustained accelerator work; aligned with job-detail GPU mean.",
  },
  avg_packetsize: {
    description:
      "Mean fabric packet payload size: total transmitted+received bytes divided by transmitted+received packets over the job.",
    researcherUse:
      "Many small messages versus bulk transfers when diagnosing MPI and fabric behavior.",
  },
  max_fabricbw: {
    description:
      "Peak fabric data rate (MB/s) from the largest interval rate of transmitted+received fabric bytes (IB/OPA preferred).",
    researcherUse:
      "Peak interconnect load for network-bound or collective-heavy phases.",
  },
  max_lnetbw: {
    description:
      "Peak Lustre LNET client data rate (MB/s) from transmitted+received LNET byte counters.",
    researcherUse:
      "Read-heavy versus write-heavy parallel I/O on the Lustre client path.",
  },
  max_mds: {
    description:
      "Peak shared-filesystem metadata or client operation rate (Lustre llite ops and/or NFS ops), in operations per second.",
    researcherUse:
      "Metadata storms alongside I/O-bound diagnosis (file-per-rank, small writes, directory-heavy scripts).",
  },
  max_packetrate: {
    description:
      "Peak fabric packet rate (packets/s) from transmitted+received packet counters.",
    researcherUse:
      "Complements average packet size for many-small-message versus bulk-transfer stories.",
  },
  max_opa_congestion_rate: {
    description:
      "Peak combined rate of Omni-Path congestion-related port counters (wait, switch congestion, FECN/BECN) over the job.",
    researcherUse:
      "Network quality versus pure byte volume on Omni-Path; pair with wait and ECN summary traces.",
  },
  max_numa_remote_rate: {
    description:
      "Peak combined rate of NUMA counters indicating non-local memory access (miss, foreign, other_node) over the job.",
    researcherUse:
      "High remote traffic suggests numactl, first-touch, or MPI rank placement fixes.",
  },
  max_gpu_power: {
    description:
      "Maximum sampled GPU power draw (watts) over the job across all GPUs and hosts.",
    researcherUse:
      "Power headroom and cooling stress on accelerators.",
  },
  max_node_power_est_w: {
    description:
      "Peak estimated on-node power (watts) from telemetry: Intel/AMD RAPL or Grace DCGM CPU plus GPU draw, or DCGM module power alone when that branch applies—not BMC or PDU.",
    researcherUse:
      "Energy-to-solution comparisons across algorithms when estimates are meaningful.",
  },
  avg_node_power_est_w: {
    description:
      "Mean estimated on-node power (watts) over samples where the estimate is defined (same construction as max_node_power_est_w).",
    researcherUse:
      "Average node power for energy-to-solution comparisons.",
  },
  max_gpu_link_gbps: {
    description:
      "Peak PCIe plus NVLink byte rate (GB/s) from DCGM PROF link counters (NVIDIA nvidia_gpu type).",
    researcherUse:
      "Staging, device-to-device traffic, and collectives using GPU-direct paths; link saturation versus compute.",
  },
  max_gpu_clock_event_reasons: {
    description:
      "Largest observed DCGM GPU clock event reasons bitmask over the job (non-zero values indicate clock throttling was reported).",
    researcherUse:
      "Nonzero values warrant correlating with temperature, power cap, or workload burstiness.",
  },
  mem_hwm: {
    description:
      "Peak resident set size (high water mark) of sampled compute processes during the job, from /proc status fields (GiB when scaled in metrics).",
    researcherUse:
      "Compare to per-node RAM for host OOM risk on larger inputs; pairs with scheduler OOM signals.",
  },
  node_imbalance: {
    description:
      "CPU utilization imbalance across nodes: maximum relative shortfall of per-node mean CPU busy time versus the busiest node.",
    researcherUse:
      "Classic load imbalance or a root process doing extra work; pair with per-node CPU and instruction metrics for stragglers.",
  },
  time_imbalance: {
    description:
      "CPU time imbalance percentage: minimum ratio of average CPU rate after versus before a sliding time slice over the job.",
    researcherUse:
      "Phases or shrinking parallelism within the job window versus steady CPU use.",
  },
  flops_node_imbalance: {
    description:
      "Maximum across nodes of mean relative shortfall in FLOP rate versus the fastest node (same construction as CPU node imbalance).",
    researcherUse:
      "Compute imbalance across nodes when FLOP counters exist, beyond utilization alone.",
  },
  fabric_node_imbalance: {
    description:
      "Maximum across nodes of mean relative shortfall in fabric byte rate versus the busiest node (InfiniBand preferred, else Omni-Path).",
    researcherUse:
      "MPI traffic unevenly distributed across nodes; rank-to-node mapping or I/O-rank effects.",
  },
  dram_bw_node_imbalance: {
    description:
      "DRAM bandwidth imbalance across nodes from AMD DF memory channels or Intel IMC CAS counters (relative shortfall versus fastest node).",
    researcherUse:
      "Different subdomain sizes, imbalanced decomposition, or one rank dominating memory bandwidth.",
  },
  lnet_node_imbalance: {
    description:
      "Lustre LNET client byte-rate imbalance across nodes (transmit plus receive counter rates).",
    researcherUse:
      "Parallel I/O imbalance on the Lustre client path.",
  },
  gpu_util_node_imbalance: {
    description:
      "GPU utilization imbalance across nodes from per-sample utilization versus the max-util GPU at each timestamp.",
    researcherUse:
      "Multi-node training: straggler GPUs or uneven batch distribution.",
  },
  tensor_node_imbalance: {
    description:
      "Tensor-pipe activity imbalance across nodes (same construction as GPU util imbalance on tensor_active).",
    researcherUse:
      "Cross-node evenness of tensor-pipeline activity for AI workloads.",
  },
  vecpercent_64b: {
    description:
      "Fraction of double-precision FLOPs delivered via vector instructions (vs scalar), from performance counters.",
    researcherUse:
      "Low vector share on FP-heavy loops points to compiler flags, data layout, or mixed precision not using wide vectors.",
  },
  vecpercent_32b: {
    description:
      "Fraction of single-precision FLOPs delivered via vector instructions (vs scalar), from performance counters.",
    researcherUse:
      "Low vector share on FP-heavy loops points to compiler flags, data layout, or mixed precision not using wide vectors.",
  },
  avg_vector_width_64b: {
    description:
      "Effective vector width for double-precision arithmetic inferred from FP_ARITH or legacy SSE counters.",
    researcherUse:
      "SIMD width mix for reasoning about vectorization versus scalar-heavy regions.",
  },
  avg_vector_width_32b: {
    description:
      "Effective vector width for single-precision arithmetic inferred from FP_ARITH or legacy SSE counters.",
    researcherUse:
      "SIMD width mix for reasoning about vectorization versus scalar-heavy regions.",
  },

  // ===== Legacy / historical metric names (not in current catalog; may appear in older data) =====
  avg_flops_32b: {
    description:
      "Legacy single-precision FLOP rate label; prefer avg_flops plus vecpercent_32b from current pipelines.",
  },
  avg_flops_64b: {
    description:
      "Legacy double-precision FLOP rate label; prefer avg_flops plus vecpercent_64b from current pipelines.",
  },
  avg_cpi: {
    description:
      "Legacy cycles-per-instruction style metric when populated (core cycles per retired instruction).",
  },
  avg_page_hitrate: {
    description:
      "Legacy cache or page-hit style metric when site pipelines populate it (not part of the default catalog).",
  },
  avg_fabricbw: {
    description:
      "Legacy label for application fabric traffic; prefer avg_ibbw / max_fabricbw in current metrics.",
  },
  avg_mdcreqs: {
    description:
      "Legacy Lustre metadata client request rate (MDC) when available.",
  },
  avg_mdcwait: {
    description:
      "Legacy average wait time for Lustre metadata client operations.",
  },
  avg_oscreqs: {
    description:
      "Legacy Lustre object storage client (OSC) request rate.",
  },
  avg_oscwait: {
    description:
      "Legacy average wait for Lustre OSC operations.",
  },
  avg_openclose: {
    description:
      "Legacy average open/close operations per second (Lustre client).",
  },
  max_load15: {
    description:
      "Legacy peak 15-minute load average over the job window.",
  },
  avg_pkg_watts: {
    description:
      "Legacy average package power (RAPL or equivalent) in watts.",
  },

  // ===== Job detail API / SPA (not always monitor-native event names) =====
  utilization: {
    description: "GPU utilization percentage sampled during the job (job detail series).",
    researcherUse:
      "Under-used GPUs may indicate wrong binding, serialization, or a CPU bottleneck ahead of the GPU.",
  },
  read_bytes: {
    description: "Bytes read via the Lustre client during the job (job detail series).",
    researcherUse:
      "Job-total read volume for checkpoint and post-processing I/O; compare with time-resolved Lustre subplots.",
  },
  write_bytes: {
    description: "Bytes written via the Lustre client during the job (job detail series).",
    researcherUse:
      "Job-total write volume for checkpoint and post-processing I/O; compare with time-resolved Lustre subplots.",
  },
};

/** Job summary Bokeh subplot column names — keep prose in sync with ``summary_metric_descriptions.py``. */
const SUMMARY_PLOT_METRIC_METADATA = {
  cpu: {
    description:
      "CPU cores in use from sampled user, system, and nice time deltas (normalized to cores; one core fully busy ≈ 1.0).",
    researcherUse:
      "Shows whether the CPU is actually busy versus waiting; correlate with I/O and network subplots.",
  },
  mem: {
    description:
      "Resident CPU memory (MemUsed) from the memory monitor, per sample and host (scaled to GiB in the plot label).",
    researcherUse:
      "Footprint and pressure versus node RAM; pairs with host OOM and mem_hwm reasoning.",
  },
  numa_remote_refs: {
    description:
      "Combined rate of NUMA counters (miss, foreign, other_node) indicating memory references served from non-local nodes.",
    researcherUse:
      "High remote NUMA traffic hurts performance: check numactl, first-touch, and MPI rank placement.",
  },
  mbw: {
    description:
      "DRAM bandwidth from Intel integrated memory controller CAS read/write counters (first IMC typename in the job schema with data), in GB/s.",
    researcherUse:
      "Memory bandwidth limits for stencil, sparse, or streaming CPU codes; pairs with the CPU roofline memory roof.",
  },
  amd_mbw: {
    description:
      "DRAM bandwidth summed across AMD Data Fabric memory channels (MBW_CHANNEL_*), in GB/s.",
    researcherUse:
      "Socket DRAM bandwidth for CPU memory-bound kernels on AMD hosts.",
  },
  amd_flops: {
    description:
      "Floating-point operation rate from AMD PMC FLOPS events (32b + 64b), in GFLOP/s.",
    researcherUse:
      "Computational intensity over time for AMD CPU paths; compare with fabric and I/O subplots.",
  },
  flops64b: {
    description:
      "Double-precision floating-point operation rate from Intel FP_ARITH or equivalent core PMCs, in GFLOP/s.",
    researcherUse:
      "Computational intensity for FP64-heavy HPC codes versus memory and interconnect limits.",
  },
  flops32b: {
    description:
      "Single-precision floating-point operation rate from Intel FP_ARITH or equivalent core PMCs, in GFLOP/s.",
    researcherUse:
      "Computational intensity for FP32-heavy regions versus memory and interconnect limits.",
  },
  instr: {
    description:
      "Retired instruction rate from core performance counters (INST_RETIRED), per second.",
    researcherUse:
      "Pairs with cycles and CPI-style metrics for instruction throughput and stall stories.",
  },
  amd_instr: {
    description:
      "Retired instruction rate from AMD core PMC (INST_RETIRED), per second.",
    researcherUse:
      "Pairs with AMD cycle counters and CPI-style metrics for throughput and stall stories.",
  },
  mcycles: {
    description: "Reference (fixed-frequency) core cycle rate from MPERF, per second.",
    researcherUse: "Reference cycle rate for CPI-style reasoning with retired instructions.",
  },
  acycles: {
    description: "Actual core cycle rate from APERF (frequency-scaled), per second.",
    researcherUse: "Actual cycles for frequency-aware CPI and stall interpretation.",
  },
  amd_mcycles: {
    description: "Reference core cycle rate from AMD MPERF, per second.",
    researcherUse: "Reference cycle rate for AMD CPI-style reasoning.",
  },
  amd_acycles: {
    description: "Actual core cycle rate from AMD APERF, per second.",
    researcherUse: "Actual AMD cycles for frequency-aware interpretation.",
  },
  freq: {
    description:
      "Effective CPU frequency (GHz) from the APERF/MPERF ratio (scaled by a fixed factor in this plot).",
    researcherUse:
      "Thermal throttling, power caps, or turbo behavior under sustained load.",
  },
  watts: {
    description:
      "Intel package power estimated from RAPL MSR_PKG_ENERGY_STATUS deltas, in watts.",
    researcherUse:
      "Package power trends versus frequency for power-limited or cooling-limited runs.",
  },
  cha_counter_arc_sum: {
    description:
      "Sum of selected Intel CHA (uncore) arc counters present in the job schema (cache/IMC related events), per second.",
    researcherUse:
      "Coarse uncore, LLC, and coherence pressure for multi-threaded or MPI+OpenMP codes.",
  },
  nv_gpu_util: {
    description:
      "GPU utilization percentage from DCGM or legacy utilization samples (NVIDIA; AMD GPU when the same fields are populated).",
    researcherUse:
      "Idle versus sustained GPU work; low util may mean a CPU preprocessing or dataloader bottleneck.",
  },
  nv_mem_used_mb: {
    description:
      "GPU device memory used (DCGM mem_used_mb), scaled to GiB in the axis label.",
    researcherUse:
      "Framebuffer headroom for GPU OOM diagnosis when correlated with failure time.",
  },
  nv_mem_util_pct: {
    description: "GPU memory utilization percentage from DCGM mem_util when available.",
    researcherUse:
      "GPU OOM risk and fragmentation patterns when viewed over time.",
  },
  nv_tensor_active: {
    description: "Tensor core / tensor pipe activity percentage from DCGM (NVIDIA).",
    researcherUse:
      "Whether time sits in tensor-heavy paths when tuning precision or framework settings.",
  },
  nv_sm_occupancy: {
    description: "Streaming multiprocessor occupancy percentage from DCGM when available.",
    researcherUse:
      "Occupancy-limited kernels versus other GPU bottlenecks.",
  },
  nv_fp16_active: {
    description: "FP16 pipeline activity percentage from DCGM when available.",
    researcherUse:
      "FP16-dominated regions when changing mixed-precision training or inference.",
  },
  nv_fp32_active: {
    description: "FP32 pipeline activity percentage from DCGM when available.",
    researcherUse:
      "FP32-dominated regions versus lower-precision paths.",
  },
  nv_gpu_mem_bw_gbs: {
    description:
      "Estimated GPU HBM/memory bandwidth from DCGM gpu_mem_bw_bytes_rate, in GB/s.",
    researcherUse:
      "Memory-bound versus compute-bound layers when HBM counters are present.",
  },
  nv_power_w: {
    description:
      "GPU board power draw summed across GPUs (DCGM power_usage), in watts.",
    researcherUse:
      "GPU power headroom, caps, and cooling stress.",
  },
  node_power_est_w: {
    description:
      "Estimated on-node power: DCGM module power when that branch applies, otherwise CPU package (RAPL or Grace DCGM) plus summed GPU draw.",
    researcherUse:
      "Blended node power for energy-to-solution comparisons when fragments allow an estimate.",
  },
  nv_gpu_link_gbs: {
    description:
      "PCIe plus NVLink byte rate from DCGM gpu_io_link_total_bytes, in GB/s.",
    researcherUse:
      "Host–device transfer and link saturation versus GPU compute; pairs with the GPU roofline when present.",
  },
  lustre_read_mb_s: {
    description:
      "Lustre client read bandwidth from llite read_bytes deltas, in MB/s (NFS is plotted separately).",
    researcherUse:
      "Read-heavy parallel I/O and checkpoint patterns; correlate with CPU idle regions.",
  },
  lustre_write_mb_s: {
    description:
      "Lustre client write bandwidth from llite write_bytes deltas, in MB/s (NFS is plotted separately).",
    researcherUse:
      "Write-heavy I/O storms and checkpoint bursts.",
  },
  liops: {
    description:
      "Lustre client metadata and inode operation rate from summed llite operation counters (open, close, getattr, etc.), per second.",
    researcherUse:
      "Metadata-heavy I/O (small files, directory storms) versus bulk read/write.",
  },
  nfs_read_mb_s: {
    description:
      "NFS client read throughput from normal, direct, and server read byte counters, in MB/s (Lustre is plotted separately).",
    researcherUse:
      "NFS read load for workflows not on Lustre; compare with Lustre subplots in mixed setups.",
  },
  nfs_write_mb_s: {
    description:
      "NFS client write throughput from normal, direct, and server write byte counters, in MB/s (Lustre is plotted separately).",
    researcherUse:
      "NFS write load alongside Lustre when both appear.",
  },
  nfs_iops: {
    description:
      "NFS client read plus write operation rate from READ_ops and WRITE_ops, per second (Lustre metadata IOPS is plotted separately).",
    researcherUse:
      "NFS operation-heavy phases versus byte-heavy streaming.",
  },
  ibbw: {
    description:
      "High-speed fabric data rate from InfiniBand extended port byte counters (receive plus transmit), in MB/s; Omni-Path may fill this when IB bytes are absent.",
    researcherUse:
      "Fabric bytes for MPI and GPU-direct traffic; correlate with CPU FLOPs and GPU util for comms-bound phases.",
  },
  summary_hardware_error_rates: {
    description:
      "Hardware error rates: each line is one counter channel summed across hosts at each sample time, using monitor counter deltas divided by elapsed time (events per second). InfiniBand, Omni-Path, and Ethernet error-style counters are included only when present in this job's schema.",
    researcherUse:
      "Spikes can flag link quality, congestion, or driver issues worth correlating with application slowdowns.",
  },
  opa_wait_cong: {
    description:
      "Omni-Path combined rate of port transmit wait and switch congestion counters.",
    researcherUse:
      "Congestion versus raw bandwidth on Omni-Path; pair with fabric bytes and MPI behavior.",
  },
  opa_ecn: {
    description: "Omni-Path combined rate of FECN and BECN receive counters.",
    researcherUse:
      "Explicit congestion signaling on Omni-Path for network-quality diagnosis.",
  },
};

/** Job Detail Bokeh figures (roofline, multiprecision) — keep prose in sync with ``job_detail_bokeh_plot_descriptions.py``. */
const JOB_DETAIL_BOKEH_PLOT_METADATA = {
  jobDetailPlot_roofline_cpu: {
    description:
      "CPU roofline: each point is one host and time sample. Horizontal axis is arithmetic intensity (GFLOP/s divided by GB/s memory bandwidth from the same sample). Vertical axis is achieved GFLOP/s. The navy curve is the theoretical roofline for this job using inferred CPU peak FLOPS and memory bandwidth.",
    researcherUse:
      "Points below the roof indicate memory-bound or imbalance behavior; points near the ridge show compute-bound phases.",
  },
  jobDetailPlot_roofline_gpu: {
    description:
      "GPU roofline: each point is one host and time sample from NVIDIA DCGM-style telemetry. Horizontal axis is arithmetic intensity (GPU GFLOP/s divided by PCIe plus NVLink byte rate in GB/s). Vertical axis is GPU GFLOP/s. The navy curve is the theoretical roof for this job using inferred GPU peaks.",
    researcherUse:
      "Use this to see whether the job is communication-limited versus GPU compute relative to the link roof.",
  },
  jobDetailPlot_multiprecision_cpu: {
    description:
      "CPU multiprecision mix: wedge areas show the fraction of CPU floating-point work attributed to each precision lane (from job-level metrics), as a share of the total that is classified.",
    researcherUse:
      "Compare FP64 versus vectorized FP32/16 activity when tuning vectorization and numerical stability.",
  },
  jobDetailPlot_multiprecision_gpu: {
    description:
      "GPU multiprecision mix: wedge areas show the fraction of GPU floating-point activity by precision (from host telemetry across the job window), as a share of classified GPU FLOPS.",
    researcherUse:
      "Use it to spot FP16-heavy kernels versus FP32/64-dominated phases.",
  },
};

export const VARIABLE_METADATA = {
  ...MONITOR_EVENT_METADATA,
  ...JOB_ACCOUNTING_AND_DERIVED_METADATA,
  ...SUMMARY_PLOT_METRIC_METADATA,
  ...JOB_DETAIL_BOKEH_PLOT_METADATA,
};

/** First token before whitespace or bracket — matches stored metric names. */
export function normalizeVariableKey(name) {
  if (name == null || typeof name !== "string") return "";
  const t = name.trim();
  if (!t) return "";
  const head = t.split(/\s+/)[0];
  return head.split("[")[0].trim();
}

/**
 * @param {string} name
 * @returns {{ description: string, researcherUse: string | null } | null}
 */
export function getVariableTooltipContent(name) {
  const key = normalizeVariableKey(name);
  if (!key) return null;
  const entry = VARIABLE_METADATA[key];
  let description;
  if (entry && entry.description && String(entry.description).trim()) {
    description = entry.description.trim();
  } else {
    description = `Telemetry variable '${key}' collected by HPCPerfStats.`;
  }
  const researcherUse =
    entry && entry.researcherUse && String(entry.researcherUse).trim()
      ? entry.researcherUse.trim()
      : null;
  return { description, researcherUse };
}

/** Plain-text tooltip body (definition plus optional researcher note, for non-React callers). */
export function getDescriptionForVariable(name) {
  const parts = getVariableTooltipContent(name);
  if (!parts) return null;
  if (parts.researcherUse) {
    return `${parts.description}\n\n—\n\n${parts.researcherUse}`;
  }
  return parts.description;
}
