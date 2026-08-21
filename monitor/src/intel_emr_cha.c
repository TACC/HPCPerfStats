/*! \file intel_emr_cha.c
 *  Intel Emerald Rapids CHA uncore via LIKWID PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_emr_cha.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_EMR_CHA_KEYS

static int intel_emr_cha_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_CHA_EMR);
}

static void intel_emr_cha_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_CHA_EMR);
}

struct stats_type intel_emr_cha_stats_type = {
    .st_name = "intel_x86_uncore_cha_emr",
    .st_begin = &intel_emr_cha_begin,
    .st_collect = &intel_emr_cha_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
