/* amd_gpu.h — schema keys for AMD GPU stats (GPUPerfAPI backend). */
#ifndef _AMD_GPU_H_
#define _AMD_GPU_H_

#define KEYS \
  X(gpu_util, "", "GPU utilization in %"), \
  X(gpu_mem_util, "", "Memory utilization in %"), \
  X(gpu_mem_total_mb, "U=MB", "Total GPU framebuffer memory (device-reported MB)"), \
  X(gpu_mem_used_mb, "U=MB", "Used GPU framebuffer memory (device-reported MB)"), \
  X(power_usage, "U=W", "Power draw in Watts"), \
  X(temperature, "U=C", "GPU temperature in C"), \
  X(fp64_active, "", "Ratio of cycles fp64 pipes are active (in %)"), \
  X(sm_active, "", "Ratio of cycles an SM has at least one wave assigned (in %)"), \
  X(sm_occupancy, "", "Ratio of active wave occupancy (in %)"), \
  X(fp32_active, "", "Ratio of cycles fp32 pipes are active (in %)"), \
  X(fp16_active, "", "Ratio of cycles fp16 pipes are active (in %)"), \
  X(tensor_active, "", "Ratio of cycles matrix/tensor pipes are active (in %)"), \
  X(clocks_event_reasons, "", "Bitmask of GPU clock slowdown reasons"), \
  X(gpu_flops_rate, "U=FLOP/s", "Estimated GPU floating-point rate (FLOP/s)"), \
  X(gpu_mem_bw_bytes_rate, "U=B/s", "Estimated GPU memory bandwidth (bytes/s)"), \
  X(gpu_flops, "E,W=64,U=FLOP", "Estimated cumulative floating-point operations"), \
  X(gpu_mem_read_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory read bytes"), \
  X(gpu_mem_write_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory write bytes"), \
  X(gpu_mem_total_bytes, "E,W=64,U=B", "Estimated cumulative GPU memory total bytes"), \
  X(gpu_count, "", "Number of GPU device rows for this type (stub uses 1)")

#endif
