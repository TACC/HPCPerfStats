#ifndef NVIDIA_GPU_ESTIMATE_H_
#define NVIDIA_GPU_ESTIMATE_H_

#include <stdint.h>

struct nvidia_gpu_estimate_input {
  double fp64_active;
  double fp32_active;
  double fp16_active;
  double tensor_active;
  double mem_util;
};

void nvidia_gpu_estimate_rates(const struct nvidia_gpu_estimate_input *in, double *flops_rate_out,
                               double *mem_bw_rate_out);

/* DCGM PROF 1009-1012 and GPM PCIe/NvLink are byte RATES (B/s), not counters: add
 * rate x delta_us into *acc (saturating) and return the bytes added; 0 when delta_us <= 0. */
unsigned long long nvidia_gpu_link_rate_integrate(uint64_t rate_bps, long long delta_us,
                                                  unsigned long long *acc);

#endif
