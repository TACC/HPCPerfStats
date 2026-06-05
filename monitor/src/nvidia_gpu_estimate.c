#include <stddef.h>
#include <stdint.h>

#include "nvidia_gpu_estimate.h"

unsigned long long nvidia_gpu_link_u64_delta(uint64_t cur, uint64_t *prev)
{
  if (prev == NULL)
    return 0;
  if (cur >= *prev) {
    unsigned long long d = (unsigned long long) (cur - *prev);

    *prev = cur;
    return d;
  }
  *prev = cur;
  return (unsigned long long) cur;
}

#define NVIDIA_GPU_APPROX_PEAK_FLOPS_PER_S 60000000000000.0
#define NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S 1000000000000.0

void nvidia_gpu_estimate_rates(const struct nvidia_gpu_estimate_input *in,
                               double *flops_rate_out, double *mem_bw_rate_out)
{
  double fp_mix;
  double flops_rate;
  double mem_bw_rate;

  if (flops_rate_out != NULL)
    *flops_rate_out = 0.0;
  if (mem_bw_rate_out != NULL)
    *mem_bw_rate_out = 0.0;
  if (in == NULL)
    return;

  fp_mix = in->fp64_active + in->fp32_active + in->fp16_active + in->tensor_active;
  if (fp_mix < 0.0)
    fp_mix = 0.0;
  if (fp_mix > 1.0)
    fp_mix = 1.0;
  flops_rate = fp_mix * NVIDIA_GPU_APPROX_PEAK_FLOPS_PER_S;
  mem_bw_rate = ((double) in->mem_util / 100.0) * NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S;
  if (mem_bw_rate < 0.0)
    mem_bw_rate = 0.0;
  if (flops_rate_out != NULL)
    *flops_rate_out = flops_rate;
  if (mem_bw_rate_out != NULL)
    *mem_bw_rate_out = mem_bw_rate;
}
