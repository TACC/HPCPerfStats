#ifndef NVIDIA_GPU_ESTIMATE_H_
#define NVIDIA_GPU_ESTIMATE_H_

struct nvidia_gpu_estimate_input {
  double fp64_active;
  double fp32_active;
  double fp16_active;
  double tensor_active;
  double mem_util;
};

void nvidia_gpu_estimate_rates(const struct nvidia_gpu_estimate_input *in,
                               double *flops_rate_out, double *mem_bw_rate_out);

#endif
