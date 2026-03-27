#ifndef _NVIDIA_GPU_H_
#define _NVIDIA_GPU_H_

#include <stdint.h>

#define NVIDIA_GPU_NFIELDS 11

#define KEYS \
  X(gpu_util, "", "GPU utilization in %"), \
  X(mem_util, "", "Memory utilization in %"), \
  X(power_usage, "U=W", "Power draw in Watts"), \
  X(temperature, "U=C", "GPU temperature in C"), \
  X(fp64_active, "", "Ratio of cycles fp64 pipes are active (in %)"), \
  X(sm_active, "", "Ratio of cycles an SM has at least one warp assigned (in %)"), \
  X(sm_occupancy, "", "Ratio of resident warps on an SM (in %)"), \
  X(fp32_active, "", "Ratio of cycles fp32 pipes are active (in %)"), \
  X(fp16_active, "", "Ratio of cycles fp16 pipes are active (in %)"), \
  X(tensor_active, "", "Ratio of cycles any tensor pipe is active (in %)"), \
  X(clocks_event_reasons, "", "Bitmask of GPU clock slowdown reasons")

typedef struct dcgm_data {
  int64_t mem_util;
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
} dcgm_data_t;

#endif
