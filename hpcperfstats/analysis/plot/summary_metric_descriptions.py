"""User-facing descriptions for job summary (Bokeh) subplot metrics.

Keep text aligned with ``SUMMARY_PLOT_METRIC_METADATA`` in
``hpcperfstats/site/frontend/src/utils/variableMetadata.js`` (same keys, same prose).
"""

from __future__ import annotations

SUMMARY_METRIC_DESCRIPTIONS: dict[str, str] = {
    "cpu": (
        "CPU cores in use from sampled user, system, and nice time deltas "
        "(normalized to cores; one core fully busy ≈ 1.0)."
    ),
    "mem": (
        "Resident CPU memory (MemUsed) from the memory monitor, per sample "
        "and host (scaled to GiB in the plot label)."
    ),
    "numa_remote_refs": (
        "Combined rate of NUMA counters (miss, foreign, other_node) indicating "
        "memory references served from non-local nodes."
    ),
    "mbw": (
        "DRAM bandwidth from Intel integrated memory controller CAS read/write "
        "counters (first IMC typename in the job schema with data), in GB/s."
    ),
    "amd_mbw": (
        "DRAM bandwidth summed across AMD Data Fabric memory channels "
        "(MBW_CHANNEL_*), in GB/s."
    ),
    "amd_flops": (
        "Floating-point operation rate from AMD PMC FLOPS events "
        "(32b + 64b), in GFLOP/s."
    ),
    "flops64b": (
        "Double-precision floating-point operation rate from Intel FP_ARITH "
        "or equivalent core PMCs, in GFLOP/s."
    ),
    "flops32b": (
        "Single-precision floating-point operation rate from Intel FP_ARITH "
        "or equivalent core PMCs, in GFLOP/s."
    ),
    "instr": (
        "Retired instruction rate from core performance counters "
        "(INST_RETIRED), per second."
    ),
    "amd_instr": (
        "Retired instruction rate from AMD core PMC (INST_RETIRED), per second."
    ),
    "mcycles": (
        "Reference (fixed-frequency) core cycle rate from MPERF, per second."
    ),
    "acycles": (
        "Actual core cycle rate from APERF (frequency-scaled), per second."
    ),
    "amd_mcycles": (
        "Reference core cycle rate from AMD MPERF, per second."
    ),
    "amd_acycles": (
        "Actual core cycle rate from AMD APERF, per second."
    ),
    "freq": (
        "Effective CPU frequency (GHz) from the APERF/MPERF ratio "
        "(scaled by a fixed factor in this plot)."
    ),
    "watts": (
        "Intel package power estimated from RAPL MSR_PKG_ENERGY_STATUS deltas, in watts."
    ),
    "cha_counter_arc_sum": (
        "Sum of selected Intel CHA (uncore) arc counters present in the job "
        "schema (cache/IMC related events), per second."
    ),
    "nv_gpu_util": (
        "GPU utilization percentage from DCGM or legacy utilization samples "
        "(NVIDIA; AMD GPU when the same fields are populated)."
    ),
    "nv_mem_used_mb": (
        "GPU device memory used (DCGM mem_used_mb), scaled to GiB in the axis label."
    ),
    "nv_mem_util_pct": (
        "GPU memory utilization percentage from DCGM mem_util when available."
    ),
    "nv_tensor_active": (
        "Tensor core / tensor pipe activity percentage from DCGM (NVIDIA)."
    ),
    "nv_sm_occupancy": (
        "Streaming multiprocessor occupancy percentage from DCGM when available."
    ),
    "nv_fp16_active": (
        "FP16 pipeline activity percentage from DCGM when available."
    ),
    "nv_fp32_active": (
        "FP32 pipeline activity percentage from DCGM when available."
    ),
    "nv_gpu_mem_bw_gbs": (
        "Estimated GPU HBM/memory bandwidth from DCGM gpu_mem_bw_bytes_rate, in GB/s."
    ),
    "nv_power_w": (
        "GPU board power draw summed across GPUs (DCGM power_usage), in watts."
    ),
    "node_power_est_w": (
        "Estimated on-node power: DCGM module power when that branch applies, "
        "otherwise CPU package (RAPL or Grace DCGM) plus summed GPU draw."
    ),
    "nv_gpu_link_gbs": (
        "PCIe plus NVLink byte rate from DCGM gpu_io_link_total_bytes, in GB/s."
    ),
    "lustre_read_mb_s": (
        "Lustre client read bandwidth from llite read_bytes deltas, in MB/s "
        "(NFS is plotted separately)."
    ),
    "lustre_write_mb_s": (
        "Lustre client write bandwidth from llite write_bytes deltas, in MB/s "
        "(NFS is plotted separately)."
    ),
    "liops": (
        "Lustre client metadata and inode operation rate from summed llite "
        "operation counters (open, close, getattr, etc.), per second."
    ),
    "nfs_read_mb_s": (
        "NFS client read throughput from normal, direct, and server read "
        "byte counters, in MB/s (Lustre is plotted separately)."
    ),
    "nfs_write_mb_s": (
        "NFS client write throughput from normal, direct, and server write "
        "byte counters, in MB/s (Lustre is plotted separately)."
    ),
    "nfs_iops": (
        "NFS client read plus write operation rate from READ_ops and WRITE_ops, "
        "per second (Lustre metadata IOPS is plotted separately)."
    ),
    "ibbw": (
        "High-speed fabric data rate from InfiniBand extended port byte "
        "counters (receive plus transmit), in MB/s; Omni-Path may fill this "
        "when IB bytes are absent."
    ),
    "fabric_mb_per_gflops": (
        "Fabric bandwidth (MB/s) divided by floating-point throughput (GFLOP/s) "
        "from the same job’s summary FLOPS column (AMD or Intel path)."
    ),
    "fabric_mb_per_avg_tensor": (
        "Fabric MB/s divided by tensor-activity percent (scaled as a fractional "
        "duty cycle) for coupled communication and GPU tensor workloads."
    ),
    "opa_wait_cong": (
        "Omni-Path combined rate of port transmit wait and switch congestion counters."
    ),
    "opa_ecn": (
        "Omni-Path combined rate of FECN and BECN receive counters."
    ),
}


def description_for_summary_metric(metric: str) -> str:
  """Return tooltip copy for a summary subplot column name."""
  key = (metric or "").strip()
  text = SUMMARY_METRIC_DESCRIPTIONS.get(key)
  if text:
    return text
  return f"Telemetry variable '{key}' collected by HPCPerfStats."
