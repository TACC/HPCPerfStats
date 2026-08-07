#include <string.h>
#include "intel_cpuid_match.h"

processor_t intel_cpuid_sig_to_processor(const char *vendor, const char *sig, int stepping)
{
  if (vendor == NULL || sig == NULL)
    return (processor_t)-1;

  if (strncmp(vendor, "GenuineIntel", 12) != 0)
    return (processor_t)-1;

  if (strncmp(sig, "06_1a", 5) == 0 || strncmp(sig, "06_1e", 5) == 0 ||
      strncmp(sig, "06_2e", 5) == 0)
    return NEHALEM;

  if (strncmp(sig, "06_25", 5) == 0 || strncmp(sig, "06_2c", 5) == 0 ||
      strncmp(sig, "06_2f", 5) == 0)
    return WESTMERE;

  if (strncmp(sig, "06_4e", 5) == 0 || strncmp(sig, "06_5e", 5) == 0)
    return SKYLAKE;

  /* LIKWID: SKYLAKEX model 0x55 — stepping < 5 is SKX, else CLX (and Cooper Lake). */
  if (strncmp(sig, "06_55", 5) == 0) {
    if (stepping < 5)
      return SKYLAKE_X;
    return CASCADE_LAKE;
  }

  if (strncmp(sig, "06_6a", 5) == 0 || strncmp(sig, "06_6c", 5) == 0)
    return ICELAKE_SERVER;

  if (strncmp(sig, "06_8f", 5) == 0)
    return SAPPHIRE_RAPIDS;

  if (strncmp(sig, "06_cf", 5) == 0)
    return EMERALD_RAPIDS;

  if (strncmp(sig, "06_ad", 5) == 0)
    return GRANITE_RAPIDS;

  if (strncmp(sig, "06_af", 5) == 0)
    return SIERRA_FOREST;

  return (processor_t)-1;
}
