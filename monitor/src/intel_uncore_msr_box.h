#ifndef INTEL_UNCORE_MSR_BOX_H_
#define INTEL_UNCORE_MSR_BOX_H_

#include <stddef.h>
#include <stdint.h>

#include "stats.h"

int intel_uncore_cbo_snb_ivb_begin_box(char *cpu, int box, uint64_t *events,
				       size_t nr_events);

void intel_uncore_cbo_snb_ivb_collect_box(struct stats_type *type, char *cpu,
					   int pkg_id, int box,
					   const char *const ctr_keys[4]);

int intel_uncore_cbo_hsw_bdw_begin_box(char *cpu, int box, uint64_t *events,
				       size_t nr_events);

void intel_uncore_cbo_hsw_bdw_collect_box(struct stats_type *type, char *cpu,
					  int pkg_id, int box,
					  const char *const ctr_keys[4]);

int intel_uncore_cha_skx_begin_box(char *cpu, int box, uint64_t *events,
				   size_t nr_events);

void intel_uncore_cha_skx_collect_box(struct stats_type *type, char *cpu,
				      int pkg_id, int box,
				      const char *const ctr_keys[4]);

#endif
