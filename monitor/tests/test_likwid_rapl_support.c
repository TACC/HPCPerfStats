/*! Vendor RAPL support gates: Intel SPR must not enable AMD RAPL helpers. */
#include <assert.h>
#include <stdio.h>

#include "cpuid.h"
#include "likwid_rapl.h"

processor_t processor;
int nr_cpus = 1;

int main(void)
{
  processor = SAPPHIRE_RAPIDS;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  assert(likwid_rapl_is_supported_amd_processor() == 0);
  assert(likwid_rapl_is_supported_processor() == 1);

  processor = AMD_19H;
  assert(likwid_rapl_is_supported_intel_processor() == 0);
  assert(likwid_rapl_is_supported_amd_processor() == 1);
  assert(likwid_rapl_is_supported_processor() == 1);

  processor = AMD_17H;
  assert(likwid_rapl_is_supported_amd_processor() == 1);
  assert(likwid_rapl_is_supported_intel_processor() == 0);

  processor = ICELAKE_SERVER;
  assert(likwid_rapl_is_supported_intel_processor() == 1);
  assert(likwid_rapl_is_supported_amd_processor() == 0);

  processor = AMD_10H;
  assert(likwid_rapl_is_supported_amd_processor() == 0);
  assert(likwid_rapl_is_supported_intel_processor() == 0);
  assert(likwid_rapl_is_supported_processor() == 0);

  printf("test_likwid_rapl_support passed\n");
  return 0;
}
