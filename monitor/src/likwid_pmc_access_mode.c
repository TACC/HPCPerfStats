/*! \file likwid_pmc_access_mode.c
 *  HPCPERFSTATS_LIKWID_ACCESS parsing for LIKWID HPMmode (PERF only).
 */

#include <strings.h>
#include <stdlib.h>

#include "likwid_pmc_access_mode.h"

likwid_pmc_access_mode_t likwid_pmc_access_mode_from_env(int *env_status)
{
  const char *v = getenv("HPCPERFSTATS_LIKWID_ACCESS");

  if (env_status != NULL)
    *env_status = LIKWID_PMC_ACCESS_ENV_OK;
  if (v == NULL || *v == '\0')
    return LIKWID_PMC_ACCESS_PERF;
  if (strcasecmp(v, "perf") == 0)
    return LIKWID_PMC_ACCESS_PERF;
  if (strcasecmp(v, "direct") == 0) {
    if (env_status != NULL)
      *env_status = LIKWID_PMC_ACCESS_ENV_DIRECT_REMOVED;
    return LIKWID_PMC_ACCESS_PERF;
  }
  if (env_status != NULL)
    *env_status = LIKWID_PMC_ACCESS_ENV_INVALID;
  return LIKWID_PMC_ACCESS_PERF;
}

const char *likwid_pmc_access_mode_name(likwid_pmc_access_mode_t mode)
{
  (void)mode;
  return "perf";
}
