#include <stdio.h>
#include <string.h>
#include "amd_cpuid_match.h"

processor_t amd_cpuid_sig_to_processor(const char *vendor, const char *sig)
{
  unsigned int fam = 0;
  unsigned int model = 0;

  if (vendor == NULL || sig == NULL)
    return (processor_t)-1;
  if (strncmp(vendor, "AuthenticAMD", 12) != 0)
    return (processor_t)-1;
  if (sscanf(sig, "%x_%x", &fam, &model) != 2)
    return (processor_t)-1;

  /* EPYC Rome: Family 17h Models 30h-3Fh (display fam 0x8f). */
  if (fam == 0x8f && model >= 0x30 && model <= 0x3f)
    return AMD_ROME;

  /* EPYC Milan: Family 19h Models 00h-0Fh (display fam 0xaf). */
  if (fam == 0xaf && model <= 0x0f)
    return AMD_MILAN;

  /* EPYC Genoa / Bergamo / Siena: Fam 19h Models 10h-1Fh and A0h-AFh. */
  if (fam == 0xaf && ((model >= 0x10 && model <= 0x1f) || (model >= 0xa0 && model <= 0xaf)))
    return AMD_GENOA;

  /* EPYC Turin (Zen5 / Zen5c): Family 1Ah Models 00h-1Fh (display fam 0xbf). */
  if (fam == 0xbf && model <= 0x1f)
    return AMD_TURIN;

  return (processor_t)-1;
}
