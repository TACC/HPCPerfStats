/*! \file likwid_pmc_force_env.c
 *  Default LIKWID_FORCE for in-use PMC overwrite (C-API equivalent of -f).
 */

#include <stdlib.h>
#include "likwid_pmc_adapter.h"

void likwid_pmc_adapter_ensure_force_env(void)
{
  /* overwrite=0: leave LIKWID_FORCE=0 (or other) if the operator already set it. */
  (void)setenv("LIKWID_FORCE", "1", 0);
}
