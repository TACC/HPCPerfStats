#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "amd_cpuid_match.h"

static void test_epyc_signatures(void)
{
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "8f_31") == AMD_ROME);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "8f_30") == AMD_ROME);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "8f_3f") == AMD_ROME);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "af_1") == AMD_MILAN);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "af_01") == AMD_MILAN);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "af_0") == AMD_MILAN);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "af_11") == AMD_GENOA);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "af_1f") == AMD_GENOA);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "af_a0") == AMD_GENOA);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "bf_2") == AMD_TURIN);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "bf_10") == AMD_TURIN);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "bf_1f") == AMD_TURIN);
}

static void test_non_epyc_rejected(void)
{
  /* Naples Fam17h Models 00h-0Fh — not Rome. */
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "8f_1") == (processor_t)-1);
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "8f_0") == (processor_t)-1);
  /* Ryzen-like Fam17h outside 30h-3Fh. */
  assert(amd_cpuid_sig_to_processor("AuthenticAMD", "8f_71") == (processor_t)-1);
  assert(amd_cpuid_sig_to_processor("GenuineIntel", "8f_31") == (processor_t)-1);
  assert(amd_cpuid_sig_to_processor(NULL, "8f_31") == (processor_t)-1);
}

int main(void)
{
  test_epyc_signatures();
  test_non_epyc_rejected();
  printf("test_amd_cpuid_match passed\n");
  return 0;
}
