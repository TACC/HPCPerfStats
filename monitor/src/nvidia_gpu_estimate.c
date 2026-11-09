#include <limits.h>
#include <stddef.h>
#include <stdint.h>

#include "nvidia_gpu_estimate.h"

unsigned long long nvidia_gpu_link_rate_integrate(uint64_t rate_bps, long long delta_us,
                                                  unsigned long long *acc)
{
  double add;
  unsigned long long bytes;

  if (acc == NULL || delta_us <= 0)
    return 0ULL;
  add = (double)rate_bps * ((double)delta_us / 1000000.0);
  bytes = (add >= (double)ULLONG_MAX) ? ULLONG_MAX : (unsigned long long)(add + 0.5);
  *acc = (ULLONG_MAX - *acc < bytes) ? ULLONG_MAX : *acc + bytes;
  return bytes;
}

#define NVIDIA_GPU_APPROX_PEAK_FLOPS_PER_S 60000000000000.0
#define NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S 1000000000000.0

void nvidia_gpu_estimate_rates(const struct nvidia_gpu_estimate_input *in, double *flops_rate_out,
                               double *mem_bw_rate_out)
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
  mem_bw_rate = ((double)in->mem_util / 100.0) * NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S;
  if (mem_bw_rate < 0.0)
    mem_bw_rate = 0.0;
  if (flops_rate_out != NULL)
    *flops_rate_out = flops_rate;
  if (mem_bw_rate_out != NULL)
    *mem_bw_rate_out = mem_bw_rate;
}
