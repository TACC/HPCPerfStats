/*! \file likwid_pmc_schema_map.h
 *  Pure LIKWID → host_cpu_hw schema key helpers (unit-testable).
 */

#ifndef _LIKWID_PMC_SCHEMA_MAP_H_
#define _LIKWID_PMC_SCHEMA_MAP_H_

#include <stddef.h>

int likwid_pmc_result_is_invalid(unsigned long long val);
/* Return 0/1/2 for FIXC0..2 (any case), else -1. */
int likwid_pmc_fixc_index(const char *counter_name);
/*
 * Map LIKWID event name to a schema key. Returns static alias string or
 * lowercase copy in buf; NULL if inputs invalid / buf too small.
 */
const char *likwid_pmc_schema_key_from_event(const char *event_name, char *buf,
					     size_t buflen);

#endif
