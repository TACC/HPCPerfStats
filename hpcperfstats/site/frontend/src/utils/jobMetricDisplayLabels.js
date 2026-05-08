/**
 * Short table labels for job-level metrics (Job detail).
 * Keys must match `job_metrics_catalog_entries()` in metrics.py.
 * Full explanations stay in variableMetadata.js (tooltip via VariableInfoLabel).
 */

const JOB_METRIC_SHORT_LABELS = {
  avg_blockbw: "Block GB/s",
  avg_cpuusage: "CPU cores",
  avg_sharedfs_iops: "FS IOPS",
  avg_sharedfs_bw: "FS MB/s",
  avg_ibbw: "IB MB/s",
  avg_fabric_mb_per_gflops: "MB/GFLOP",
  avg_tensor_active: "Tensor %",
  avg_fp16_active: "FP16 %",
  avg_fp32_active: "FP32 %",
  avg_fp64_active: "FP64 %",
  avg_gpu_mem_bw_gbps: "GPU HBM",
  avg_fabric_mb_per_avg_tensor: "MB/tensor",
  avg_flops: "GFLOP/s",
  avg_mbw: "DRAM GB/s",
  avg_freq: "CPU GHz",
  avg_ethbw: "Eth MB/s",
  detail_gpu_active: "GPU active",
  detail_gpu_util_max: "GPU max %",
  detail_gpu_util_mean: "GPU mean %",
  detail_gpu_count: "GPU count",
  detail_fsio_llite_read_mb: "FSIO llite read",
  detail_fsio_llite_write_mb: "FSIO llite write",
  detail_fsio_llite_peak_mb_s: "FSIO llite peak MB/s",
  detail_fsio_llite_peak_iops: "FSIO llite peak IOPS",
  detail_fsio_nfs_read_mb: "FSIO NFS read",
  detail_fsio_nfs_write_mb: "FSIO NFS write",
  detail_fsio_nfs_peak_mb_s: "FSIO NFS peak MB/s",
  detail_fsio_nfs_peak_iops: "FSIO NFS peak IOPS",
  avg_gpuutil: "GPU %",
  avg_packetsize: "Pkt size",
  max_fabricbw: "Fab peak",
  max_lnetbw: "LNET peak",
  max_mds: "MDS peak",
  max_packetrate: "Pkt/s peak",
  max_opa_congestion_rate: "OPA cong",
  max_numa_remote_rate: "NUMA rem",
  max_gpu_power: "GPU W max",
  max_node_power_est_w: "Node W max",
  avg_node_power_est_w: "Node W avg",
  max_gpu_link_gbps: "GPU link",
  max_gpu_clock_event_reasons: "GPU clk",
  mem_hwm: "RSS HWM",
  node_imbalance: "CPU imbal",
  time_imbalance: "Time imbal",
  flops_node_imbalance: "FLOP imbal",
  fabric_node_imbalance: "Fab imbal",
  dram_bw_node_imbalance: "DRAM imbal",
  lnet_node_imbalance: "LNET imbal",
  gpu_util_node_imbalance: "GPU imbal",
  tensor_node_imbalance: "Tensor imbal",
  vecpercent_64b: "Vec% DP",
  avg_vector_width_64b: "VW DP",
  vecpercent_32b: "Vec% SP",
  avg_vector_width_32b: "VW SP",
};

export function getJobMetricShortLabel(metric) {
  if (metric == null || typeof metric !== "string") return "";
  return JOB_METRIC_SHORT_LABELS[metric] ?? metric;
}

export { JOB_METRIC_SHORT_LABELS };
