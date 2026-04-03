#ifndef _NVIDIA_GPU_H_
#define _NVIDIA_GPU_H_

#include <stdint.h>

#define NVIDIA_GPU_NFIELDS 17

#define KEYS \
  X(gpu_util, "", "GPU utilization in %"), \
  X(mem_util, "", "Memory utilization in %"), \
  X(mem_total_mb, "U=MB", "Total GPU framebuffer memory (device-reported MB)"), \
  X(mem_used_mb, "U=MB", "Used GPU framebuffer memory (device-reported MB)"), \
  X(power_usage, "U=W", "Power draw in Watts"), \
  X(temperature, "U=C", "GPU temperature in C"), \
  X(fp64_active, "", "Ratio of cycles fp64 pipes are active (in %)"), \
  X(sm_active, "", "Ratio of cycles an SM has at least one warp assigned (in %)"), \
  X(sm_occupancy, "", "Ratio of resident warps on an SM (in %)"), \
  X(fp32_active, "", "Ratio of cycles fp32 pipes are active (in %)"), \
  X(fp16_active, "", "Ratio of cycles fp16 pipes are active (in %)"), \
  X(tensor_active, "", "Ratio of cycles any tensor pipe is active (in %)"), \
  X(clocks_event_reasons, "", "Bitmask of GPU clock slowdown reasons"), \
  X(gpu_flops_rate, "U=FLOP/s", "Estimated GPU floating-point rate (FLOP/s)"), \
  X(gpu_mem_bw_bytes_rate, "U=B/s", "Estimated GPU memory bandwidth (bytes/s)"), \
  X(gpu_flops, "E,W=64,U=FLOP", "Estimated cumulative floating-point operations"), \
  X(gpu_mem_read_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory read bytes"), \
  X(gpu_mem_write_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory write bytes"), \
  X(gpu_mem_total_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory total bytes"), \
  X(gpu_io_link_total_bytes, "E,W=64,U=B", "Cumulative PCIe plus NvLink link bytes (DCGM PROF TX/RX; not HBM/DRAM framebuffer)"), \
  X(gpu_count, "", "Number of GPUs on this node (DCGM-visible; same value on each device row)")

typedef struct dcgm_data {
  int64_t mem_util;
  int64_t fb_total_mb;
  int64_t fb_used_mb;
  int64_t gpu_util;
  int64_t temperature;
  int64_t clocks_event_reasons;
  double power_usage;
  double tensor_active;
  double fp64_active;
  double fp32_active;
  double fp16_active;
  double sm_active;
  double sm_occupancy;
  uint64_t prof_pcie_tx_bytes;
  uint64_t prof_pcie_rx_bytes;
  uint64_t prof_nvlink_tx_bytes;
  uint64_t prof_nvlink_rx_bytes;
} dcgm_data_t;

#endif
