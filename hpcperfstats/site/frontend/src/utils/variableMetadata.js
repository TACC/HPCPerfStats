/**
 * Tooltip copy keyed by metric / event short name (before units in brackets).
 *
 * Merge order: monitor `host_data.event` names (see variableMetadataMonitorEvents.js),
 * then job accounting / derived metrics / API-only fields (this file). Later entries win
 * on purpose so UI-specific wording overrides generic monitor text.
 */

import { MONITOR_EVENT_METADATA } from "./variableMetadataMonitorEvents.js";

const JOB_ACCOUNTING_AND_DERIVED_METADATA = {
  // ===== Job accounting fields (job_data model) =====
  jid: { description: "Slurm job ID (unique identifier for the job)." },
  username: { description: "Username of the job owner from Slurm accounting." },
  account: { description: "Project/account charged for the job in Slurm accounting." },
  start_time: { description: "Timestamp when the job started running." },
  end_time: { description: "Timestamp when the job finished running." },
  runtime: { description: "Elapsed runtime of the job in seconds." },
  timelimit: { description: "Requested walltime limit for the job in seconds." },
  queue: { description: "Queue/partition the job ran in." },
  jobname: { description: "Job name as submitted to the scheduler." },
  state: { description: "Final scheduler state for the job (e.g., COMPLETED, FAILED)." },
  ncores: { description: "Number of CPU cores allocated to the job." },
  nhosts: { description: "Number of nodes allocated to the job." },
  metrics_distinct_time_count: {
    description:
      "Sum over job hosts of distinct sample timestamps in host_data for this job’s time window, as recorded when job-level metrics were last persisted. This is not the raw row count of host_data.",
  },

  // ===== Job-level derived metrics (hpcperfstats/analysis/metrics/metrics.py catalog) =====
  avg_blockbw: {
    description:
      "Average block-device throughput computed from read and write sectors over the job (GB/s).",
  },
  avg_cpuusage: {
    description:
      "Average number of CPU cores busy, inferred from cumulative user/system/nice CPU time deltas over the job window.",
  },
  avg_sharedfs_iops: {
    description:
      "Average metadata and I/O operation rate for the shared filesystem named in the Shared File System section on this page (Lustre llite and/or NFS READ/WRITE operation counters over the job).",
  },
  avg_sharedfs_bw: {
    description:
      "Average read+write bandwidth for the shared filesystem named in the Shared File System section on this page (Lustre llite and/or NFS byte counters over the job).",
  },
  avg_ibbw: {
    description:
      "Average high-speed fabric bandwidth from InfiniBand extended counters, else Omni-Path, else summed Ethernet bytes (MB/s).",
  },
  avg_fabric_mb_per_gflops: {
    description:
      "Ratio of average fabric bandwidth (MB/s) to average floating-point throughput (GFLOP/s), using the same job-mean fabric and FLOP paths as avg_ibbw and avg_flops.",
  },
  avg_tensor_active: {
    description:
      "Average DCGM tensor-pipe activity percentage (NVIDIA preferred; AMD GPU when the same field is populated).",
  },
  avg_gpu_mem_bw_gbps: {
    description:
      "Average estimated GPU HBM/memory bandwidth rate from DCGM (GB/s), job-mean across hosts and time buckets.",
  },
  avg_fabric_mb_per_avg_tensor: {
    description:
      "Heuristic ratio: average fabric MB/s divided by average tensor-activity percent (scaled to a fractional duty cycle), for coupled MPI plus GPU workloads.",
  },
  avg_flops: {
    description:
      "Average floating-point throughput (GFLOP/s) from AMD FLOPS PMC, Intel FP_ARITH or legacy SSE proxies, or ARM synthetic counters.",
  },
  avg_mbw: {
    description:
      "Average DRAM memory bandwidth (GB/s) from AMD Data Fabric channels, Intel IMC CAS counters, ARM IMC, or synthetic ARM/DCGM bytes.",
  },

  avg_freq: {
    description:
      "Average effective CPU frequency (GHz) from APERF/MPERF or equivalent PMC ratios over the job.",
  },
  avg_ethbw: {
    description:
      "Average Ethernet bandwidth computed from per-interface receive+transmit byte counters over the job (MB/s).",
  },
  avg_gpuutil: {
    description:
      "Average GPU utilization percentage from sampled GPU utilization telemetry over the job.",
  },
  avg_packetsize: {
    description:
      "Mean fabric packet payload size: total transmitted+received bytes divided by transmitted+received packets over the job.",
  },
  max_fabricbw: {
    description:
      "Peak fabric data rate (MB/s) from the largest interval rate of transmitted+received fabric bytes (IB/OPA preferred).",
  },
  max_lnetbw: {
    description:
      "Peak Lustre LNET client data rate (MB/s) from transmitted+received LNET byte counters.",
  },
  max_mds: {
    description:
      "Peak shared-filesystem metadata or client operation rate (Lustre llite ops and/or NFS ops), in operations per second.",
  },
  max_packetrate: {
    description:
      "Peak fabric packet rate (packets/s) from transmitted+received packet counters.",
  },
  max_opa_congestion_rate: {
    description:
      "Peak combined rate of Omni-Path congestion-related port counters (wait, switch congestion, FECN/BECN) over the job.",
  },
  max_numa_remote_rate: {
    description:
      "Peak combined rate of NUMA counters indicating non-local memory access (miss, foreign, other_node) over the job.",
  },
  max_gpu_power: {
    description:
      "Maximum sampled GPU power draw (watts) over the job across all GPUs and hosts.",
  },
  max_node_power_est_w: {
    description:
      "Peak estimated on-node power (watts) from telemetry: Intel/AMD RAPL or Grace DCGM CPU plus GPU draw, or DCGM module power alone when that branch applies—not BMC or PDU.",
  },
  avg_node_power_est_w: {
    description:
      "Mean estimated on-node power (watts) over samples where the estimate is defined (same construction as max_node_power_est_w).",
  },
  max_gpu_link_gbps: {
    description:
      "Peak PCIe plus NVLink byte rate (GB/s) from DCGM PROF link counters (NVIDIA nvidia_gpu type).",
  },
  max_gpu_clock_event_reasons: {
    description:
      "Largest observed DCGM GPU clock event reasons bitmask over the job (non-zero values indicate clock throttling was reported).",
  },
  mem_hwm: {
    description:
      "Peak resident set size (high water mark) of sampled compute processes during the job, from /proc status fields (GiB when scaled in metrics).",
  },
  node_imbalance: {
    description:
      "CPU utilization imbalance across nodes: maximum relative shortfall of per-node mean CPU busy time versus the busiest node.",
  },
  time_imbalance: {
    description:
      "CPU time imbalance percentage: minimum ratio of average CPU rate after versus before a sliding time slice over the job.",
  },
  flops_node_imbalance: {
    description:
      "Maximum across nodes of mean relative shortfall in FLOP rate versus the fastest node (same construction as CPU node imbalance).",
  },
  fabric_node_imbalance: {
    description:
      "Maximum across nodes of mean relative shortfall in fabric byte rate versus the busiest node (InfiniBand preferred, else Omni-Path).",
  },
  dram_bw_node_imbalance: {
    description:
      "DRAM bandwidth imbalance across nodes from AMD DF memory channels or Intel IMC CAS counters (relative shortfall versus fastest node).",
  },
  lnet_node_imbalance: {
    description:
      "Lustre LNET client byte-rate imbalance across nodes (transmit plus receive counter rates).",
  },
  gpu_util_node_imbalance: {
    description:
      "GPU utilization imbalance across nodes from per-sample utilization versus the max-util GPU at each timestamp.",
  },
  tensor_node_imbalance: {
    description:
      "Tensor-pipe activity imbalance across nodes (same construction as GPU util imbalance on tensor_active).",
  },
  vecpercent_64b: {
    description:
      "Fraction of double-precision FLOPs delivered via vector instructions (vs scalar), from performance counters.",
  },
  vecpercent_32b: {
    description:
      "Fraction of single-precision FLOPs delivered via vector instructions (vs scalar), from performance counters.",
  },
  avg_vector_width_64b: {
    description:
      "Effective vector width for double-precision arithmetic inferred from FP_ARITH or legacy SSE counters.",
  },
  avg_vector_width_32b: {
    description:
      "Effective vector width for single-precision arithmetic inferred from FP_ARITH or legacy SSE counters.",
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
  },
  read_bytes: {
    description: "Bytes read via the Lustre client during the job (job detail series).",
  },
  write_bytes: {
    description: "Bytes written via the Lustre client during the job (job detail series).",
  },
};

/** Job summary Bokeh subplot column names — keep prose in sync with ``summary_metric_descriptions.py``. */
const SUMMARY_PLOT_METRIC_METADATA = {
  cpu: {
    description:
      "CPU cores in use from sampled user, system, and nice time deltas (normalized to cores; one core fully busy ≈ 1.0).",
  },
  mem: {
    description:
      "Resident CPU memory (MemUsed) from the memory monitor, per sample and host (scaled to GiB in the plot label).",
  },
  numa_remote_refs: {
    description:
      "Combined rate of NUMA counters (miss, foreign, other_node) indicating memory references served from non-local nodes.",
  },
  mbw: {
    description:
      "DRAM bandwidth from Intel integrated memory controller CAS read/write counters (first IMC typename in the job schema with data), in GB/s.",
  },
  amd_mbw: {
    description:
      "DRAM bandwidth summed across AMD Data Fabric memory channels (MBW_CHANNEL_*), in GB/s.",
  },
  amd_flops: {
    description:
      "Floating-point operation rate from AMD PMC FLOPS events (32b + 64b), in GFLOP/s.",
  },
  flops64b: {
    description:
      "Double-precision floating-point operation rate from Intel FP_ARITH or equivalent core PMCs, in GFLOP/s.",
  },
  flops32b: {
    description:
      "Single-precision floating-point operation rate from Intel FP_ARITH or equivalent core PMCs, in GFLOP/s.",
  },
  instr: {
    description:
      "Retired instruction rate from core performance counters (INST_RETIRED), per second.",
  },
  amd_instr: {
    description:
      "Retired instruction rate from AMD core PMC (INST_RETIRED), per second.",
  },
  mcycles: {
    description: "Reference (fixed-frequency) core cycle rate from MPERF, per second.",
  },
  acycles: {
    description: "Actual core cycle rate from APERF (frequency-scaled), per second.",
  },
  amd_mcycles: {
    description: "Reference core cycle rate from AMD MPERF, per second.",
  },
  amd_acycles: {
    description: "Actual core cycle rate from AMD APERF, per second.",
  },
  freq: {
    description:
      "Effective CPU frequency (GHz) from the APERF/MPERF ratio (scaled by a fixed factor in this plot).",
  },
  watts: {
    description:
      "Intel package power estimated from RAPL MSR_PKG_ENERGY_STATUS deltas, in watts.",
  },
  cha_counter_arc_sum: {
    description:
      "Sum of selected Intel CHA (uncore) arc counters present in the job schema (cache/IMC related events), per second.",
  },
  nv_gpu_util: {
    description:
      "GPU utilization percentage from DCGM or legacy utilization samples (NVIDIA; AMD GPU when the same fields are populated).",
  },
  nv_mem_used_mb: {
    description:
      "GPU device memory used (DCGM mem_used_mb), scaled to GiB in the axis label.",
  },
  nv_mem_util_pct: {
    description: "GPU memory utilization percentage from DCGM mem_util when available.",
  },
  nv_tensor_active: {
    description: "Tensor core / tensor pipe activity percentage from DCGM (NVIDIA).",
  },
  nv_sm_occupancy: {
    description: "Streaming multiprocessor occupancy percentage from DCGM when available.",
  },
  nv_fp16_active: {
    description: "FP16 pipeline activity percentage from DCGM when available.",
  },
  nv_fp32_active: {
    description: "FP32 pipeline activity percentage from DCGM when available.",
  },
  nv_gpu_mem_bw_gbs: {
    description:
      "Estimated GPU HBM/memory bandwidth from DCGM gpu_mem_bw_bytes_rate, in GB/s.",
  },
  nv_power_w: {
    description:
      "GPU board power draw summed across GPUs (DCGM power_usage), in watts.",
  },
  node_power_est_w: {
    description:
      "Estimated on-node power: DCGM module power when that branch applies, otherwise CPU package (RAPL or Grace DCGM) plus summed GPU draw.",
  },
  nv_gpu_link_gbs: {
    description:
      "PCIe plus NVLink byte rate from DCGM gpu_io_link_total_bytes, in GB/s.",
  },
  lustre_read_mb_s: {
    description:
      "Lustre client read bandwidth from llite read_bytes deltas, in MB/s (NFS is plotted separately).",
  },
  lustre_write_mb_s: {
    description:
      "Lustre client write bandwidth from llite write_bytes deltas, in MB/s (NFS is plotted separately).",
  },
  liops: {
    description:
      "Lustre client metadata and inode operation rate from summed llite operation counters (open, close, getattr, etc.), per second.",
  },
  nfs_read_mb_s: {
    description:
      "NFS client read throughput from normal, direct, and server read byte counters, in MB/s (Lustre is plotted separately).",
  },
  nfs_write_mb_s: {
    description:
      "NFS client write throughput from normal, direct, and server write byte counters, in MB/s (Lustre is plotted separately).",
  },
  nfs_iops: {
    description:
      "NFS client read plus write operation rate from READ_ops and WRITE_ops, per second (Lustre metadata IOPS is plotted separately).",
  },
  ibbw: {
    description:
      "High-speed fabric data rate from InfiniBand extended port byte counters (receive plus transmit), in MB/s; Omni-Path may fill this when IB bytes are absent.",
  },
  fabric_mb_per_gflops: {
    description:
      "Fabric bandwidth (MB/s) divided by floating-point throughput (GFLOP/s) from the same job’s summary FLOPS column (AMD or Intel path).",
  },
  fabric_mb_per_avg_tensor: {
    description:
      "Fabric MB/s divided by tensor-activity percent (scaled as a fractional duty cycle) for coupled communication and GPU tensor workloads.",
  },
  opa_wait_cong: {
    description:
      "Omni-Path combined rate of port transmit wait and switch congestion counters.",
  },
  opa_ecn: {
    description: "Omni-Path combined rate of FECN and BECN receive counters.",
  },
};

export const VARIABLE_METADATA = {
  ...MONITOR_EVENT_METADATA,
  ...JOB_ACCOUNTING_AND_DERIVED_METADATA,
  ...SUMMARY_PLOT_METRIC_METADATA,
};

/** First token before whitespace or bracket — matches stored metric names. */
export function normalizeVariableKey(name) {
  if (name == null || typeof name !== "string") return "";
  const t = name.trim();
  if (!t) return "";
  const head = t.split(/\s+/)[0];
  return head.split("[")[0].trim();
}

export function getDescriptionForVariable(name) {
  const key = normalizeVariableKey(name);
  if (!key) return null;
  const entry = VARIABLE_METADATA[key];
  if (entry && entry.description && String(entry.description).trim()) {
    return entry.description.trim();
  }
  return `Telemetry variable '${key}' collected by HPCPerfStats.`;
}
