#ifndef _LIKWID_UNCORE_ADAPTER_H_
#define _LIKWID_UNCORE_ADAPTER_H_

#include "likwid_uncore_profiles.h"
#include "stats.h"

int likwid_uncore_adapter_begin(struct stats_type *type, likwid_uncore_profile_t profile);
void likwid_uncore_adapter_collect(struct stats_type *type, likwid_uncore_profile_t profile);
void likwid_uncore_adapter_emit_counter(struct stats_type *type, likwid_uncore_profile_t profile,
                                        const char *counter_name, unsigned long long val);

/* Records, once per profile, the devices and keys the eventset `events` programs (from its
 * COUNTER names); -1 when a counter maps to no schema key or the device table is full. */
int likwid_uncore_adapter_plan(struct stats_type *type, likwid_uncore_profile_t profile,
                               const char *events);
/* NOT_BUILT on every key of each planned device that the eventset does not program. */
void likwid_uncore_adapter_mark_unprogrammed(struct stats_type *type,
                                             likwid_uncore_profile_t profile);

#endif
