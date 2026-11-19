/* CPUID processor classification (Intel and AMD EPYC). */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "trace.h"
#include "cpuid.h"
#include "intel_cpuid_match.h"
#include "amd_cpuid_match.h"

#if defined(__i386__) || defined(__x86_64__)

#define cpuid(func, ax, bx, cx, dx)                                                                \
  __asm__ __volatile__("cpuid" : "=a"(ax), "=b"(bx), "=c"(cx), "=d"(dx) : "a"(func))

processor_t signature(int *n_pmcs)
{
  uint32_t eax = 0, ebx = 0, ecx = 0, edx = 0;
  char vendor[13];
  processor_t rc = (processor_t)-1;

  if (n_pmcs == NULL)
    return rc;
  *n_pmcs = 0;
  cpuid(0, eax, ebx, ecx, edx);
  snprintf(vendor, sizeof(vendor), "%c%c%c%c%c%c%c%c%c%c%c%c", ebx & 0xff, (ebx >> 8) & 0xff,
           (ebx >> 16) & 0xff, (ebx >> 24) & 0xff, edx & 0xff, (edx >> 8) & 0xff,
           (edx >> 16) & 0xff, (edx >> 24) & 0xff, ecx & 0xff, (ecx >> 8) & 0xff,
           (ecx >> 16) & 0xff, (ecx >> 24) & 0xff);
  TRACE("vendor %s\n", vendor);

  cpuid(1, eax, ebx, ecx, edx);
  char sig[16];
  int model = (eax & 0x0FF) >> 4;
  int extended_model = (eax & 0xF0000) >> 12;
  int family_code = (eax & 0xF00) >> 8;
  int extended_family_code = (eax & 0xFF00000) >> 16;
  int stepping = (int)(eax & 0xF);
  snprintf(sig, sizeof(sig), "%02x_%x", extended_family_code | family_code, extended_model | model);
  TRACE("sig %s stepping %d\n", sig, stepping);

  if (strncmp(vendor, "GenuineIntel", 12) == 0) {
    cpuid(0x0A, eax, ebx, ecx, edx);
    *n_pmcs = (eax >> 8) & 0xFF;
    TRACE("Number of PMCs = %d\n", *n_pmcs);

    rc = intel_cpuid_sig_to_processor(vendor, sig, stepping);
    if (rc != (processor_t)-1) {
      TRACE("Intel processor sig %s stepping %d -> %d\n", sig, stepping, (int)rc);
      return rc;
    }
  } else if (strncmp(vendor, "AuthenticAMD", 12) == 0) {
    rc = amd_cpuid_sig_to_processor(vendor, sig);
    if (rc != (processor_t)-1) {
      *n_pmcs = 6;
      TRACE("AMD EPYC processor sig %s -> %d (n_pmcs=6)\n", sig, (int)rc);
      return rc;
    }
  }

  return rc;
}

#else /* !(__i386__ || __x86_64__): no CPUID instruction (e.g. AArch64 with nvc++/gcc) */

processor_t signature(int *n_pmcs)
{
  if (n_pmcs != NULL)
    *n_pmcs = 0;
  TRACE("cpuid: non-x86 host; processor signature unavailable\n");
  return (processor_t)-1;
}

#endif
