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

/* Monotonic DCGM link-byte delta; resets baseline when counter decreases. */
unsigned long long nvidia_gpu_link_u64_delta(uint64_t cur, uint64_t *prev);

#endif
