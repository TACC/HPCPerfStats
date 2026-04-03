/**
 * Human-readable descriptions keyed by metric / event name (short form, no units suffix).
 * Sourced from docs/attributes-definition.md — Definition and Description columns combined:
 * prefer Description when non-empty, else Definition.
 */

export const VARIABLE_METADATA = {
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

  // ===== Derived job-level metrics (docs/attributes-definition.md) =====
  avg_cpuusage: {
    description:
      "Number of cores in use throughout job, averaged across duration",
  },
  mem_hwm: {
    description:
      "The highest amount of memory in use at one time",
  },
  avg_flops_32b: {
    description:
      "Average Single Precision Floating Point Operations Per Second (GF)",
  },
  avg_vector_width_32b: {
    description:
      "How well your code is vectorizing.",
  },
  avg_flops_64b: {
    description:
      "Avergae Double Precision Floating Point Operations Per Second (GF)",
  },
  avg_vector_width_64b: {
    description:
      "How well your code is vectorizing.",
  },
  avg_cpi: {
    description: "How many cycles until instruction is pushed",
  },
  avg_freq: {
    description: "Average Frequency (GHz)",
  },
  avg_mbw: {
    description: "Average Memory Bandwidth (GB/s)",
  },
  avg_page_hitrate: {
    description:
      "How much communication you're doing over the network.",
  },
  avg_fabricbw: {
    description:
      "Measuring all of the communication over the high-speed network.",
  },
  max_fabricbw: {
    description:
      "Application network traffic that is not IO to the parallel file system.",
  },
  avg_packetsize: {
    description:
      "How big the messages are that are being communicated",
  },
  max_packetrate: {
    description:
      "If your packet rate is high, that might be a problem. Unsure of what a packet consists of and limited by. Heo might know.",
  },
  avg_ethbw: {
    description: "Average Ethernet Bandwidth (MB/s)",
  },
  max_mds: {
    description:
      "How many metadata operations - files opened/closed/seeked",
  },
  avg_mdcreqs: {
    description: "Average Metadata C Requests",
  },
  avg_mdcwait: {
    description: "Average Metadata C Request Wait Time",
  },
  avg_oscreqs: {
    description: "Average OST Requests Per Second",
  },
  avg_oscwait: {
    description: "Average Wait for OST Requests",
  },
  avg_openclose: {
    description: "Average Open/Close Per Second",
  },
  avg_blockbw: {
    description: "Average Block Bandwidth (MB/s)",
  },
  max_load15: {
    description: "Maximum Load Per [Interval]",
  },
  avg_pkg_watts: {
    description:
      "Average Package Power Consumption (CPU only for most machines and architectures. For Vista and sapphire rapids it includes GPU too). Package = chip and all the things attached to the chip. The package of purely computational components.",
  },

  // ===== Derived job-level metrics (from code inspection: analysis/metrics/metrics.py) =====
  avg_blockbw: {
    description:
      "Average block-device throughput computed from read and write sectors over the job.",
  },
  avg_sharedfs_iops: {
    description:
      "Average metadata and I/O operation rate for the shared filesystem named in the Shared File System section on this page (from Lustre client and/or NFS counters over the job).",
  },
  avg_sharedfs_bw: {
    description:
      "Average read+write bandwidth for the shared filesystem named in the Shared File System section on this page (from Lustre client and/or NFS byte counters over the job).",
  },
  avg_ibbw: {
    description:
      "Average fabric (InfiniBand/OPA) bandwidth computed from transmitted and received data counters over the job.",
  },
  avg_fabric_mb_per_gflops: {
    description:
      "Ratio of average fabric bandwidth (MB/s) to average floating-point throughput (GFLOP/s), using the same job-mean fabric and FLOPs paths as avg_ibbw and avg_flops.",
  },
  max_opa_congestion_rate: {
    description:
      "Peak combined rate of Omni-Path congestion-related port counters (wait, switch congestion, FECN/BECN) over the job.",
  },
  max_numa_remote_rate: {
    description:
      "Peak combined rate of NUMA counters indicating non-local memory access (miss, foreign, other_node) over the job.",
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
      "DRAM bandwidth imbalance across nodes from AMD DF memory channels or Intel IMC CAS counters (same relative shortfall construction as fabric node imbalance).",
  },
  lnet_node_imbalance: {
    description:
      "Lustre LNET client byte-rate imbalance across nodes (transmit plus receive counter rates).",
  },
  avg_tensor_active: {
    description:
      "Average DCGM tensor-pipe activity percentage (NVIDIA preferred; AMD GPU when the same field is populated).",
  },
  avg_gpu_mem_bw_gbps: {
    description:
      "Average estimated GPU HBM/memory bandwidth rate from DCGM (GB/s), job-mean across hosts and time buckets.",
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
      "Largest observed DCGM GPU clock event reasons bitmask over the job (non-zero values indicate some clock throttling was reported).",
  },
  gpu_util_node_imbalance: {
    description:
      "GPU utilization imbalance across nodes from per-sample utilization versus the max-util GPU at each timestamp.",
  },
  tensor_node_imbalance: {
    description:
      "Tensor-pipe activity imbalance across nodes (same construction as GPU util imbalance on tensor_active).",
  },
  avg_fabric_mb_per_avg_tensor: {
    description:
      "Heuristic ratio: average fabric MB/s divided by average tensor-activity percent (scaled to a fractional duty cycle), for coupled MPI plus GPU workloads.",
  },
  avg_flops: {
    description:
      "Average floating-point throughput computed from hardware floating-point performance counters over the job.",
  },
  avg_freq: {
    description:
      "Average CPU frequency computed from performance counter ratios over the job.",
  },
  avg_ethbw: {
    description:
      "Average Ethernet bandwidth computed from network receive+transmit byte counters over the job.",
  },
  avg_gpuutil: {
    description:
      "Average GPU utilization percentage computed from sampled GPU utilization telemetry over the job.",
  },
  avg_packetsize: {
    description:
      "Average fabric packet size computed as transmitted+received bytes divided by transmitted+received packets over the job.",
  },
  max_fabricbw: {
    description:
      "Maximum fabric bandwidth computed from the peak interval rate of transmitted+received fabric data over the job.",
  },
  max_lnetbw: {
    description:
      "Maximum LNET bandwidth computed from the peak interval rate of transmitted+received LNET bytes over the job.",
  },
  max_mds: {
    description:
      "Maximum Lustre metadata operation rate computed from the peak interval rate of Lustre client metadata counters over the job.",
  },
  max_packetrate: {
    description:
      "Maximum fabric packet rate computed from the peak interval rate of transmitted+received fabric packets over the job.",
  },
  time_imbalance: {
    description:
      "CPU time imbalance percentage computed as the minimum ratio of average CPU rate after vs before a time slice over the job.",
  },
  vecpercent_64b: {
    description:
      "Percent of double-precision floating-point operations attributed to vector instructions (vs scalar) from performance counters.",
  },
  vecpercent_32b: {
    description:
      "Percent of single-precision floating-point operations attributed to vector instructions (vs scalar) from performance counters.",
  },

  // ===== Job detail / host_data events (from code inspection: API + monitor schema) =====
  // From API job_detail: GPU utilization and Lustre read/write bytes.
  utilization: {
    description: "GPU utilization percentage sampled during the job.",
  },
  read_bytes: {
    description: "Bytes read via the Lustre client during the job.",
  },
  write_bytes: {
    description: "Bytes written via the Lustre client during the job.",
  },

  // From monitor schema (monitor/src/*.c): CPU time counters.
  user: { description: "CPU time spent in user mode." },
  system: { description: "CPU time spent in system mode." },
  nice: { description: "CPU time spent in user mode at low priority." },
  idle: { description: "CPU time spent idle." },
  iowait: { description: "CPU time spent waiting for I/O." },
  irq: { description: "CPU time spent handling interrupts." },
  softirq: { description: "CPU time spent handling soft interrupts." },

  // From monitor schema: block device counters.
  rd_sectors: { description: "Number of 512-byte sectors read by the block device." },
  wr_sectors: { description: "Number of 512-byte sectors written by the block device." },
  rd_ios: { description: "Number of read requests processed by the block device." },
  wr_ios: { description: "Number of write requests processed by the block device." },
  rd_merges: { description: "Number of read requests merged by the block device." },
  wr_merges: { description: "Number of write requests merged by the block device." },
  rd_ticks: { description: "Time spent waiting for block read requests (ms)." },
  wr_ticks: { description: "Time spent waiting for block write requests (ms)." },
  io_ticks: { description: "Time the block device spent doing I/O (ms)." },
  time_in_queue: { description: "Time spent in the block device I/O queue (ms)." },
  in_flight: { description: "Number of block I/O requests currently in flight." },

  // From monitor schema: network bytes (used by avg_ethbw).
  rx_bytes: { description: "Bytes received on the network interface." },
  tx_bytes: { description: "Bytes transmitted on the network interface." },

  // From monitor schema: InfiniBand extended counters (used by avg_ibbw/max_*).
  port_xmit_data: { description: "Fabric data transmitted." },
  port_rcv_data: { description: "Fabric data received." },
  port_xmit_pkts: { description: "Fabric packets transmitted." },
  port_rcv_pkts: { description: "Fabric packets received." },

  // From monitor schema: load averages.
  load_1: { description: "1-minute load average (scaled by 100)." },
  load_5: { description: "5-minute load average (scaled by 100)." },
  load_15: { description: "15-minute load average (scaled by 100)." },
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
  // Fallback so every Job Detail variable can show a tooltip without guessing specifics.
  return `Telemetry variable '${key}' collected by HPCPerfStats.`;
}
