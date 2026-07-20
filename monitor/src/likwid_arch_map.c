/*! \file likwid_arch_map.c
 *  Resolve HPCPERFSTATS_MONITOR_ARCH / cpuinfo to a LIKWID event string.
 */

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "likwid_arch_map.h"

static const char *intel_eventset(void)
{
  return "INSTR_RETIRED_ANY:FIXC0,CPU_CLK_UNHALTED_CORE:FIXC1,CPU_CLK_UNHALTED_REF:FIXC2,"
         "MEM_LOAD_UOPS_RETIRED_L1_HIT:PMC0,MEM_LOAD_UOPS_RETIRED_L2_HIT:PMC1,"
         "MEM_LOAD_UOPS_RETIRED_LLC_HIT:PMC2,L1D_REPLACEMENT:PMC3";
}

static const char *amd_eventset(void)
{
  return "RETIRED_INSTRUCTIONS:PMC0,RETIRED_BRANCH_INSTR:PMC1,RETIRED_MISP_BRANCH_INSTR:PMC2,"
         "LS_DISPATCH:PMC3";
}

static int str_eq_nocase(const char *a, const char *b)
{
  unsigned char ca, cb;
  if (a == NULL || b == NULL)
    return 0;
  while (*a != '\0' && *b != '\0') {
    ca = (unsigned char) *a++;
    cb = (unsigned char) *b++;
    if (tolower(ca) != tolower(cb))
      return 0;
  }
  return *a == '\0' && *b == '\0';
}

static int cpu_vendor_is_amd_like(void)
{
  FILE *f;
  char line[256];

  f = fopen("/proc/cpuinfo", "re");
  if (f == NULL)
    return 0;

  while (fgets(line, sizeof(line), f) != NULL) {
    if (strncmp(line, "vendor_id", 9) != 0)
      continue;
    if (strstr(line, "AuthenticAMD") != NULL || strstr(line, "HygonGenuine") != NULL) {
      fclose(f);
      return 1;
    }
    break;
  }

  fclose(f);
  return 0;
}

static const char *intel_icx_eventset(void)
{
  return "INSTR_RETIRED_ANY:FIXC0,CPU_CLK_UNHALTED_CORE:FIXC1,CPU_CLK_UNHALTED_REF:FIXC2,"
         "MEM_INST_RETIRED_ALL_LOADS:PMC0,L1D_REPLACEMENT:PMC1,"
         "MEM_INST_RETIRED_ALL_STORES:PMC2";
}

static const char *intel_spr_eventset(int n_pmcs)
{
  /* SPR: use ICX-validated LIKWID names. SKX-era MEM_LOAD_UOPS_RETIRED_*
   * are missing on SPR in LIKWID 5.5.x; extra FP PMCs often hit "in use". */
  (void)n_pmcs;
  return intel_icx_eventset();
}

const char *likwid_arch_eventset_for_processor(processor_t p, int n_pmcs)
{
  switch (p) {
  case ICELAKE_SERVER:
    return intel_icx_eventset();
  case SAPPHIRE_RAPIDS:
    return intel_spr_eventset(n_pmcs);
  case SKYLAKE:
  case CASCADE_LAKE:
  case NEHALEM:
  case WESTMERE:
    return intel_eventset();
  default:
    return intel_eventset();
  }
}

const char *likwid_arch_eventset(void)
{
  const char *arch_env = getenv("HPCPERFSTATS_MONITOR_ARCH");
  if (arch_env != NULL) {
    if (str_eq_nocase(arch_env, "amd"))
      return amd_eventset();
    if (str_eq_nocase(arch_env, "intel"))
      return intel_eventset();
  }

  if (cpu_vendor_is_amd_like())
    return amd_eventset();

  return intel_eventset();
}
