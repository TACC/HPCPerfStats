/*! \file likwid_pmc_schema_map.h
 *  Pure LIKWID to host_cpu_hw schema key helpers (unit-testable).
 */

#ifndef _LIKWID_PMC_SCHEMA_MAP_H_
#define _LIKWID_PMC_SCHEMA_MAP_H_

#include <stddef.h>

#include "stats_sentinel.h"

struct schema;

int likwid_pmc_result_is_invalid(unsigned long long val);
/* Return 0/1/2 for FIXC0..2 (any case), else -1. */
int likwid_pmc_fixc_index(const char *counter_name);
/*
 * Map LIKWID event name to a schema key. Returns static alias string or
 * lowercase copy in buf; NULL if inputs invalid / buf too small.
 */
const char *likwid_pmc_schema_key_from_event(const char *event_name, char *buf, size_t buflen);
/*
 * Sentinel for a host_cpu_hw key that the LIKWID eventset does not program: NOT_PRESENT when the
 * key belongs to another architecture (arm backend keys) or the other x86 vendor (Intel-only on
 * AMD and the reverse), else NOT_BUILT (applies to this CPU, but this build does not count it).
 */
enum stats_sentinel likwid_pmc_unprogrammed_code(const char *key, int is_amd);
/*
 * codes[k] for every key of @p sc: STATS_SENTINEL_OK when @p event_string ("EVENT:COUNTER,...")
 * programs it (directly, through FIXC0..2, or as the instr_retired / aperf / mperf semantic
 * copy), else likwid_pmc_unprogrammed_code(). @return 0, or -1 on invalid input.
 */
int likwid_pmc_key_codes(struct schema *sc, const char *event_string, int is_amd,
                         unsigned char *codes);

#endif
