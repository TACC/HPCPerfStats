/* Pure NVML / GPM unit converters for nvidia_gpu (see nvidia_gpu_nvml_convert.h). */
#include <math.h>
#include <stddef.h>
#include <stdint.h>

#include "nvidia_gpu_nvml_convert.h"

#define NVIDIA_GPU_NVML_PCT_SLACK 0.5

int nvidia_gpu_nvml_pct_to_ratio(double pct, double *out)
{
  if (out == NULL || !isfinite(pct) || pct < -NVIDIA_GPU_NVML_PCT_SLACK ||
      pct > 100.0 + NVIDIA_GPU_NVML_PCT_SLACK)
    return -1;
  if (pct < 0.0)
    pct = 0.0;
  if (pct > 100.0)
    pct = 100.0;
  *out = pct * 0.01;
  return 0;
}

uint64_t nvidia_gpu_nvml_mib_per_s_to_bytes(double mib_per_s)
{
  double b;

  if (!isfinite(mib_per_s) || mib_per_s <= 0.0)
    return 0;
  b = mib_per_s * NVIDIA_GPU_NVML_MIB;
  if (b >= 18446744073709549568.0) /* largest double below 2^64 */
    return UINT64_MAX;
  return (uint64_t)(b + 0.5);
}

double nvidia_gpu_nvml_mw_to_w(unsigned int mw)
{
  return (double)mw / 1000.0;
}

int64_t nvidia_gpu_nvml_bytes_to_mib(unsigned long long bytes)
{
  return (int64_t)(bytes / 1048576ULL);
}
