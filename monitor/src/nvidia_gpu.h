#ifndef _NVIDIA_GPU_H_
#define _NVIDIA_GPU_H_

#include <stdint.h>

#define NVIDIA_GPU_NFIELDS 25
/* DCGM field-group size without the optional tensor IMMA/HMMA/DFMA split and integer pipe
 * (1013-1016; older host engines). */
#define NVIDIA_GPU_DCGM_NCORE 21
/* Minimal non-PROF list for stacks where DCGM profiling watches are not permissioned/supported. */
#define NVIDIA_GPU_DCGM_NBASIC 10

#define KEYS                                                                                       \
  X(gpu_util, "", "GPU utilization in %"), X(gpu_mem_util, "", "Memory utilization in %"),         \
      X(gpu_mem_total_mb, "U=MB", "Total GPU framebuffer memory (device-reported MB)"),            \
      X(gpu_mem_used_mb, "U=MB", "Used GPU framebuffer memory (device-reported MB)"),              \
      X(gpu_mem_free_mb, "U=MB", "Free GPU framebuffer memory (device-reported MB)"),              \
      X(gpu_sm_clock, "", "SM clock from DCGM (device-reported units)"),                           \
      X(power_usage, "U=W", "Power draw in Watts"), X(temperature, "U=C", "GPU temperature in C"), \
      X(fp64_active, "", "Ratio of cycles fp64 pipes are active (in %)"),                          \
      X(sm_active, "", "Ratio of cycles an SM has at least one warp assigned (in %)"),             \
      X(sm_occupancy, "", "Ratio of resident warps on an SM (in %)"),                              \
      X(fp32_active, "", "Ratio of cycles fp32 pipes are active (in %)"),                          \
      X(fp16_active, "", "Ratio of cycles fp16 pipes are active (in %)"),                          \
      X(tensor_active, "", "Ratio of cycles any tensor pipe is active (in %)"),                    \
      X(tensor_imma_active, "",                                                                    \
        "DCGM tensor IMMA pipe duty cycle (in %); int/low-precision tensor path - use as "         \
        "FP8-adjacent signal only, not dedicated FP8 FLOPs"),                                      \
      X(tensor_hmma_active, "",                                                                    \
        "Tensor HMMA pipe duty cycle (in %); FP16/BF16/TF32/FP8 MMA, counted in gpu_flops at the " \
        "dense FP16 tensor peak"),                                                                 \
      X(tensor_dfma_active, "",                                                                    \
        "DFMA pipe duty cycle (in %); the FP64 FMA pipe (equals fp64_active on Hopper and "        \
        "Blackwell), not FP64 tensor; excluded from gpu_flops"),                                   \
      X(tensor_dmma_active, "",                                                                    \
        "Tensor DMMA pipe duty cycle (in %); FP64 tensor MMA, counted in gpu_flops (GPM only; "    \
        "not present on the DCGM path)"),                                                          \
      X(integer_active, "",                                                                        \
        "Ratio of cycles the integer pipe is active (in %); excluded from gpu_flops"),             \
      X(gpu_dram_active, "", "Ratio of cycles device memory interface is active (in %)"),          \
      X(gpu_pcie_tx_bytes, "E,W=64,U=B", "PCIe transmit bytes (integrated rate, monotonic)"),      \
      X(gpu_pcie_rx_bytes, "E,W=64,U=B", "PCIe receive bytes (integrated rate, monotonic)"),       \
      X(gpu_nvlink_tx_bytes, "E,W=64,U=B", "NvLink transmit bytes (integrated rate, monotonic)"),  \
      X(gpu_nvlink_rx_bytes, "E,W=64,U=B", "NvLink receive bytes (integrated rate, monotonic)"),   \
      X(gpu_pcie_replay_counter, "E,W=64", "DCGM PCIe replay counter (monotonic)"),                \
      X(clocks_event_reasons, "", "Bitmask of GPU clock slowdown reasons"),                        \
      X(gpu_flops_rate, "U=FLOP/s",                                                                \
        "Estimated GPU floating-point rate: pipe duty cycles x per-SM peaks for the compute "      \
        "capability x SM count x SM clock (FLOP/s)"),                                              \
      X(gpu_mem_bw_bytes_rate, "U=B/s", "Estimated GPU memory bandwidth (bytes/s)"),               \
      X(gpu_flops, "E,W=64,U=FLOP", "Estimated cumulative floating-point operations"),             \
      X(gpu_mem_read_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory read bytes"),           \
      X(gpu_mem_write_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory write bytes"),         \
      X(gpu_mem_total_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory total bytes"),         \
      X(gpu_io_link_total_bytes, "E,W=64,U=B",                                                     \
        "Cumulative PCIe plus NvLink link bytes (DCGM PROF TX/RX; not HBM/DRAM framebuffer)"),     \
      X(gpu_count, "",                                                                             \
        "Number of GPUs on this node (DCGM-visible; same value on each device row)")

/* Keys of one GPU row that could not be read this collect, with their value sentinel. */
#define NVIDIA_GPU_ROW_MISS_MAX 32
struct nvidia_gpu_row_miss {
  const char *key;
  unsigned char code; /* enum stats_sentinel */
};

typedef struct dcgm_data {
  int64_t mem_util;
  int64_t fb_total_mb;
  int64_t fb_used_mb;
  int64_t fb_free_mb;
  int64_t gpu_util;
  int64_t temperature;
  int64_t sm_clock;
  int64_t pcie_replay_counter;
  int64_t clocks_event_reasons;
  double power_usage;
  double tensor_active;
  double tensor_imma_active;
  double tensor_hmma_active;
  double tensor_dfma_active;
  double tensor_dmma_active;
  double integer_active;
  double dram_active;
  double fp64_active;
  double fp32_active;
  double fp16_active;
  double sm_active;
  double sm_occupancy;
  uint64_t prof_pcie_tx_bytes;
  uint64_t prof_pcie_rx_bytes;
  uint64_t prof_nvlink_tx_bytes;
  uint64_t prof_nvlink_rx_bytes;
  /* Device shape for gpu_flops (nvidia_gpu_nvml_row_shape); 0 = unknown. */
  int cc_major;
  int cc_minor;
  unsigned int sm_count;  /* NVML multiprocessor count */
  unsigned int gpu_cores; /* NVML CUDA core count, used when sm_count is unknown */
  struct nvidia_gpu_row_miss miss[NVIDIA_GPU_ROW_MISS_MAX];
  int nmiss;
  uint32_t seen; /* DCGM path: one bit per field the fetch delivered (nvidia_gpu_dcgm_field.c) */
} dcgm_data_t;

#endif
