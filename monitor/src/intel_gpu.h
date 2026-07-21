/* intel_gpu.h — schema keys for Intel Data Center GPU (PVC) via XPU Manager. */
#ifndef _INTEL_GPU_H_
#define _INTEL_GPU_H_

#define KEYS                                                                                       \
  X(gpu_util, "", "GPU utilization in %"), X(gpu_mem_util, "", "Memory utilization in %"),         \
      X(gpu_mem_total_mb, "U=MB", "Total GPU memory (device-reported MB)"),                        \
      X(gpu_mem_used_mb, "U=MB", "Used GPU memory (device-reported MB)"),                          \
      X(power_usage, "U=W", "Power draw in Watts"),                                                \
      X(temperature, "U=C", "GPU core temperature in C"),                                          \
      X(gpu_sm_clock, "", "GPU EU/frequency clock in MHz (schema kinship with nvidia_gpu)"),       \
      X(sm_active, "", "EU Array Active %"),                                                       \
      X(gpu_dram_active, "", "Memory bandwidth utilization %"),                                    \
      X(gpu_pcie_rx_bytes, "E,W=64,U=B", "PCIe read byte counter (monotonic when XPUM exposes)"),  \
      X(gpu_pcie_tx_bytes, "E,W=64,U=B", "PCIe write byte counter (monotonic when XPUM exposes)"), \
      X(gpu_xe_link_rx_bytes, "E,W=64,U=B", "Xe Link / fabric received bytes (accumulated)"),      \
      X(gpu_xe_link_tx_bytes, "E,W=64,U=B", "Xe Link / fabric transmitted bytes (accumulated)"),   \
      X(clocks_event_reasons, "", "Frequency throttle reason flags when available"),               \
      X(gpu_count, "", "Number of XPUM-visible Intel GPUs on this node")

struct stats_type;
extern struct stats_type intel_gpu_stats_type;

#endif
