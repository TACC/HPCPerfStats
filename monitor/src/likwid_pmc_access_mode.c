/*! \file likwid_pmc_access_mode.c
 *  HPCPERFSTATS_LIKWID_ACCESS parsing for LIKWID HPMmode.
 */

#include <strings.h>
#include <stdlib.h>

#include "likwid_pmc_access_mode.h"

likwid_pmc_access_mode_t likwid_pmc_access_mode_from_env(int *invalid)
{
  const char *v = getenv("HPCPERFSTATS_LIKWID_ACCESS");

  if (invalid != NULL)
    *invalid = 0;
  if (v == NULL || *v == '\0')
    return LIKWID_PMC_ACCESS_PERF;
  if (strcasecmp(v, "perf") == 0)
    return LIKWID_PMC_ACCESS_PERF;
  if (strcasecmp(v, "direct") == 0)
    return LIKWID_PMC_ACCESS_DIRECT;
  if (invalid != NULL)
    *invalid = 1;
  return LIKWID_PMC_ACCESS_PERF;
}

const char *likwid_pmc_access_mode_name(likwid_pmc_access_mode_t mode)
{
  if (mode == LIKWID_PMC_ACCESS_DIRECT)
    return "direct";
  return "perf";
}
