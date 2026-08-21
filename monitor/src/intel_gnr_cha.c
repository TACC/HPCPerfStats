/*! \file intel_gnr_cha.c
 *  Intel Granite Rapids CHA uncore via LIKWID PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_gnr_cha.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_GNR_CHA_KEYS

static int intel_gnr_cha_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_CHA_GNR);
}

static void intel_gnr_cha_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_CHA_GNR);
}

struct stats_type intel_gnr_cha_stats_type = {
    .st_name = "intel_x86_uncore_cha_gnr",
    .st_begin = &intel_gnr_cha_begin,
    .st_collect = &intel_gnr_cha_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
