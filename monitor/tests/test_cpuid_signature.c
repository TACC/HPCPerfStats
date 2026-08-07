#include <assert.h>
#include <stdio.h>
#include "intel_cpuid_match.h"
#include "amd_cpuid_match.h"

static void test_intel_signatures(void)
{
  /* SKX vs CLX: LIKWID splits model 0x55 by stepping. */
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_55", 0) == SKYLAKE_X);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_55", 4) == SKYLAKE_X);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_55", 5) == CASCADE_LAKE);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_55", 7) == CASCADE_LAKE);

  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_4e", 0) == SKYLAKE);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_6a", 0) == ICELAKE_SERVER);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_6c", 0) == ICELAKE_SERVER);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_8f", 0) == SAPPHIRE_RAPIDS);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_cf", 0) == EMERALD_RAPIDS);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_ad", 0) == GRANITE_RAPIDS);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_af", 0) == SIERRA_FOREST);
}

static void test_retired_generations_unknown(void)
{
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_2a", 0) == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_3a", 0) == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_3c", 0) == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_3d", 0) == (processor_t)-1);
}

static void test_unknown(void)
{
  assert(intel_cpuid_sig_to_processor("AuthenticAMD", "06_55", 5) == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor("GenuineIntel", "06_99", 0) == (processor_t)-1);
  assert(intel_cpuid_sig_to_processor(NULL, "06_55", 5) == (processor_t)-1);
  assert(amd_cpuid_sig_to_processor("GenuineIntel", "8f_31") == (processor_t)-1);
}

int main(void)
{
  test_intel_signatures();
  test_retired_generations_unknown();
  test_unknown();
  return 0;
}
