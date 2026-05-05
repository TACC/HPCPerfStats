/*! \file amd64_event_tables.h
 *  Shared AMD PMC / DF event encodings (Zen-family duplication folded here).
 */

#ifndef AMD64_EVENT_TABLES_H
#define AMD64_EVENT_TABLES_H

#include <stdint.h>

#ifdef MONITOR_LEGACY_PMCS
extern const uint64_t amd64_pmc_events_10h[4];
#endif
extern const uint64_t amd64_pmc_events_zen[6];
extern const uint64_t amd64_df_dram_events[4];

#endif
