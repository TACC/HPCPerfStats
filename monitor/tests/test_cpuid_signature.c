#include <assert.h>
#include <stdio.h>
#include "intel_cpuid_match.h"

static void test_intel_signatures(void)
{
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_55") == CASCADE_LAKE);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_4e") == SKYLAKE);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_6a") == ICELAKE_SERVER);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_6c") == ICELAKE_SERVER);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_8f") == SAPPHIRE_RAPIDS);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_2a") == SANDYBRIDGE);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_3c") == HASWELL);
}

static void test_unknown(void)
{
  assert(intel_cpuid_sig_to_processor("AuthenticAMD", "06_55") == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_99") == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor(NULL, "06_55") == (processor_t)-1);
}

int main(void)
{
  test_intel_signatures();
  test_unknown();
  return 0;
}
