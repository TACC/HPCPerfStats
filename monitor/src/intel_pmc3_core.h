#ifndef INTEL_PMC3_CORE_H_
#define INTEL_PMC3_CORE_H_

#include "stats.h"

int intel_pmc3_core_begin_if_pmcs(struct stats_type *type, int need_pmcs);

void intel_pmc3_core_foreach_cpu(struct stats_type *type,
				 void (*collect_cpu)(struct stats_type *,
						     char *));

#endif
