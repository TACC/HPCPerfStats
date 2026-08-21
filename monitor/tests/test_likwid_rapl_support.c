#include <assert.h>
#include <stdio.h>
#include "likwid_rapl.h"
#include "cpuid.h"

processor_t processor;
int nr_cpus = 1;

int main(void)
{
  processor = AMD_MILAN;
  assert(likwid_rapl_is_supported_amd_processor() == 1);
  assert(likwid_rapl_is_supported_processor() == 1);

  processor = AMD_ROME;
  assert(likwid_rapl_is_supported_amd_processor() == 1);

  processor = AMD_GENOA;
  assert(likwid_rapl_is_supported_amd_processor() == 1);

  processor = AMD_TURIN;
  assert(likwid_rapl_is_supported_amd_processor() == 1);
  /* Regression: MONITOR_ARCH_INTEL build must still pick AMD PWR path on Turin. */
  assert(likwid_rapl_collect_path() == LIKWID_RAPL_PATH_AMD);

  processor = AMD_10H;
  assert(likwid_rapl_is_supported_amd_processor() == 0);
  assert(likwid_rapl_collect_path() == LIKWID_RAPL_PATH_NONE);

  processor = SAPPHIRE_RAPIDS;
  assert(likwid_rapl_is_supported_amd_processor() == 0);
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  assert(likwid_rapl_collect_path() == LIKWID_RAPL_PATH_INTEL);

  processor = SKYLAKE_X;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  processor = CASCADE_LAKE;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  processor = EMERALD_RAPIDS;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  processor = GRANITE_RAPIDS;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  processor = SIERRA_FOREST;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  assert(likwid_rapl_collect_path() == LIKWID_RAPL_PATH_INTEL);

  printf("test_likwid_rapl_support passed\n");
  return 0;
}
