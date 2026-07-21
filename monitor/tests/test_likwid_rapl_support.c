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

  processor = AMD_10H;
  assert(likwid_rapl_is_supported_amd_processor() == 0);

  processor = SAPPHIRE_RAPIDS;
  assert(likwid_rapl_is_supported_amd_processor() == 0);
  assert(likwid_rapl_is_supported_intel_processor() == 1);

  printf("test_likwid_rapl_support passed\n");
  return 0;
}
