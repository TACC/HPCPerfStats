/*! \file amd64_pmu_core.h
 *  Shared MSR programming for AMD core PMC and Data Fabric PMC selects.
 */

#ifndef AMD64_PMU_CORE_H
#define AMD64_PMU_CORE_H

#include <stdint.h>

int amd64_pmu_msr_program_selects(char *cpu, uint64_t ctl0_msr,
				  const uint64_t *events, int n_events);

int amd64_pmu_core_program_counters_with_hwcr(char *cpu,
					      const uint64_t *events,
					      int n_events);

#endif
