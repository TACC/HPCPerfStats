#ifndef _LIKWID_UNCORE_ADAPTER_H_
#define _LIKWID_UNCORE_ADAPTER_H_

#include "likwid_uncore_profiles.h"
#include "stats.h"

int likwid_uncore_adapter_begin(struct stats_type *type, likwid_uncore_profile_t profile);
void likwid_uncore_adapter_collect(struct stats_type *type, likwid_uncore_profile_t profile);
void likwid_uncore_adapter_emit_counter(struct stats_type *type, likwid_uncore_profile_t profile,
                                        const char *counter_name, unsigned long long val);

#endif
