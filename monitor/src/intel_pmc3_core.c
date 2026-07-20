/*! \file intel_pmc3_core.c
 *  Shared Intel PMC3 begin/foreach helpers.
 */

#include "intel_pmc3_core.h"

#include <stdio.h>

#include "intel_pmc3.h"

int intel_pmc3_core_begin_if_pmcs(struct stats_type *type, int need_pmcs)
{
  int nr = 0;
  int i;

  if (n_pmcs != need_pmcs)
    goto out;

  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];

    snprintf(cpu, sizeof(cpu), "%d", i);
    if (intel_pmc3_begin_cpu(cpu) == 0)
      nr++;
  }

out:
  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

void intel_pmc3_core_foreach_cpu(struct stats_type *type,
                                 void (*collect_cpu)(struct stats_type *, char *))
{
  int i;

  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];

    snprintf(cpu, sizeof(cpu), "%d", i);
    collect_cpu(type, cpu);
  }
}
