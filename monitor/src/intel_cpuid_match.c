#include <string.h>
#include "intel_cpuid_match.h"

processor_t intel_cpuid_sig_to_processor(const char *vendor, const char *sig)
{
  if (vendor == NULL || sig == NULL)
    return (processor_t)-1;

  if (strncmp(vendor, "GenuineIntel", 12) != 0)
    return (processor_t)-1;

  if (strncmp(sig, "06_1a", 5) == 0 ||
      strncmp(sig, "06_1e", 5) == 0 ||
      strncmp(sig, "06_2e", 5) == 0)
    return NEHALEM;

  if (strncmp(sig, "06_25", 5) == 0 ||
      strncmp(sig, "06_2c", 5) == 0 ||
      strncmp(sig, "06_2f", 5) == 0)
    return WESTMERE;

  if (strncmp(sig, "06_3a", 5) == 0 ||
      strncmp(sig, "06_3e", 5) == 0)
    return IVYBRIDGE;

  if (strncmp(sig, "06_2a", 5) == 0 ||
      strncmp(sig, "06_2d", 5) == 0)
    return SANDYBRIDGE;

  if (strncmp(sig, "06_3c", 5) == 0 ||
      strncmp(sig, "06_45", 5) == 0 ||
      strncmp(sig, "06_46", 5) == 0 ||
      strncmp(sig, "06_3f", 5) == 0)
    return HASWELL;

  if (strncmp(sig, "06_3d", 5) == 0 ||
      strncmp(sig, "06_47", 5) == 0 ||
      strncmp(sig, "06_4f", 5) == 0)
    return BROADWELL;

  if (strncmp(sig, "06_4e", 5) == 0 ||
      strncmp(sig, "06_5e", 5) == 0)
    return SKYLAKE;

  if (strncmp(sig, "06_55", 5) == 0)
    return CASCADE_LAKE;

  if (strncmp(sig, "06_6a", 5) == 0 ||
      strncmp(sig, "06_6c", 5) == 0)
    return ICELAKE_SERVER;

  if (strncmp(sig, "06_8f", 5) == 0)
    return SAPPHIRE_RAPIDS;

  return (processor_t)-1;
}
