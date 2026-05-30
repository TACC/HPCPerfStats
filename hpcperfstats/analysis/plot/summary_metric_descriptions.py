"""User-facing descriptions for job summary (Bokeh) subplot metrics.

Keep ``SUMMARY_METRIC_DESCRIPTIONS`` aligned with ``description`` strings in
``SUMMARY_PLOT_METRIC_METADATA`` in
``hpcperfstats/site/frontend/src/utils/variableMetadata.js`` (same keys, same prose).

Keep ``SUMMARY_METRIC_RESEARCHER_USE`` aligned with ``researcherUse`` in the same
JS object (HPC / AI / diagnostic reasoning from ``docs/using-the-website-as-a-researcher.md``).
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
    "summary_hardware_error_rates": (
        "Hardware error rates: each line is one counter channel summed across hosts at "
        "each sample time, using monitor counter deltas divided by elapsed time (events "
        "per second). InfiniBand, Omni-Path, and Ethernet error-style counters are "
        "included only when present in this job's schema."
    ),
    "opa_wait_cong": (
        "Omni-Path combined rate of port transmit wait and switch congestion counters."
    ),
    "opa_ecn": (
        "Omni-Path combined rate of FECN and BECN receive counters."
    ),
}

SUMMARY_METRIC_RESEARCHER_USE: dict[str, str] = {
    "cpu": (
        "Shows whether the CPU is actually busy versus waiting; correlate with "
        "I/O and network subplots."
    ),
    "mem": (
        "Footprint and pressure versus node RAM; pairs with host OOM and mem_hwm reasoning."
    ),
    "numa_remote_refs": (
        "High remote NUMA traffic hurts performance: check numactl, first-touch, "
        "and MPI rank placement."
    ),
    "mbw": (
        "Memory bandwidth limits for stencil, sparse, or streaming CPU codes; pairs "
        "with the CPU roofline memory roof."
    ),
    "amd_mbw": "Socket DRAM bandwidth for CPU memory-bound kernels on AMD hosts.",
    "amd_flops": (
        "Computational intensity over time for AMD CPU paths; compare with fabric "
        "and I/O subplots."
    ),
    "flops64b": (
        "Computational intensity for FP64-heavy HPC codes versus memory and "
        "interconnect limits."
    ),
    "flops32b": (
        "Computational intensity for FP32-heavy regions versus memory and "
        "interconnect limits."
    ),
    "instr": (
        "Pairs with cycles and CPI-style metrics for instruction throughput and stall stories."
    ),
    "amd_instr": (
        "Pairs with AMD cycle counters and CPI-style metrics for throughput and stall stories."
    ),
    "mcycles": "Reference cycle rate for CPI-style reasoning with retired instructions.",
    "acycles": "Actual cycles for frequency-aware CPI and stall interpretation.",
    "amd_mcycles": "Reference cycle rate for AMD CPI-style reasoning.",
    "amd_acycles": "Actual AMD cycles for frequency-aware interpretation.",
    "freq": (
        "Thermal throttling, power caps, or turbo behavior under sustained load."
    ),
    "watts": (
        "Package power trends versus frequency for power-limited or cooling-limited runs."
    ),
    "cha_counter_arc_sum": (
        "Coarse uncore, LLC, and coherence pressure for multi-threaded or MPI+OpenMP codes."
    ),
    "nv_gpu_util": (
        "Idle versus sustained GPU work; low util may mean a CPU preprocessing or "
        "dataloader bottleneck."
    ),
    "nv_mem_used_mb": (
        "Framebuffer headroom for GPU OOM diagnosis when correlated with failure time."
    ),
    "nv_mem_util_pct": (
        "GPU OOM risk and fragmentation patterns when viewed over time."
    ),
    "nv_tensor_active": (
        "Whether time sits in tensor-heavy paths when tuning precision or framework settings."
    ),
    "nv_sm_occupancy": (
        "Occupancy-limited kernels versus other GPU bottlenecks."
    ),
    "nv_fp16_active": (
        "FP16-dominated regions when changing mixed-precision training or inference."
    ),
    "nv_fp32_active": (
        "FP32-dominated regions versus lower-precision paths."
    ),
    "nv_gpu_mem_bw_gbs": (
        "Memory-bound versus compute-bound layers when HBM counters are present."
    ),
    "nv_power_w": "GPU power headroom, caps, and cooling stress.",
    "node_power_est_w": (
        "Blended node power for energy-to-solution comparisons when fragments allow "
        "an estimate."
    ),
    "nv_gpu_link_gbs": (
        "Host–device transfer and link saturation versus GPU compute; pairs with the "
        "GPU roofline when present."
    ),
    "lustre_read_mb_s": (
        "Read-heavy parallel I/O and checkpoint patterns; correlate with CPU idle regions."
    ),
    "lustre_write_mb_s": "Write-heavy I/O storms and checkpoint bursts.",
    "liops": (
        "Metadata-heavy I/O (small files, directory storms) versus bulk read/write."
    ),
    "nfs_read_mb_s": (
        "NFS read load for workflows not on Lustre; compare with Lustre subplots "
        "in mixed setups."
    ),
    "nfs_write_mb_s": "NFS write load alongside Lustre when both appear.",
    "nfs_iops": "NFS operation-heavy phases versus byte-heavy streaming.",
    "ibbw": (
        "Fabric bytes for MPI and GPU-direct traffic; correlate with CPU FLOPs and "
        "GPU util for comms-bound phases."
    ),
    "summary_hardware_error_rates": (
        "Spikes can flag link quality, congestion, or driver issues worth correlating "
        "with application slowdowns."
    ),
    "opa_wait_cong": (
        "Congestion versus raw bandwidth on Omni-Path; pair with fabric bytes and "
        "MPI behavior."
    ),
    "opa_ecn": (
        "Explicit congestion signaling on Omni-Path for network-quality diagnosis."
    ),
}


def description_for_summary_metric(metric: str) -> str:
  """Return tooltip copy for a summary subplot column name."""
  key = (metric or "").strip()
  text = SUMMARY_METRIC_DESCRIPTIONS.get(key)
  if text:
    return text
  return f"Telemetry variable '{key}' collected by HPCPerfStats."


def researcher_use_for_summary_metric(metric: str) -> str | None:
  """Optional researcher-facing guidance (same keys as ``SUMMARY_METRIC_RESEARCHER_USE``)."""
  key = (metric or "").strip()
  return SUMMARY_METRIC_RESEARCHER_USE.get(key)
