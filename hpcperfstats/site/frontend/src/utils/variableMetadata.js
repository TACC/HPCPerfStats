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

export const VARIABLE_METADATA = {
  ...MONITOR_EVENT_METADATA,
  ...JOB_ACCOUNTING_AND_DERIVED_METADATA,
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
