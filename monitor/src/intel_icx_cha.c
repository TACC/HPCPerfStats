/*! \file intel_icx_cha.c
 *  Intel Ice Lake server CHA uncore via LIKWID PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_icx_cha.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_ICX_CHA_KEYS

static int intel_icx_cha_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_CHA_ICX);
}

static void intel_icx_cha_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_CHA_ICX);
}

struct stats_type intel_icx_cha_stats_type = {
    .st_name = "intel_x86_uncore_cha_icx",
    .st_begin = &intel_icx_cha_begin,
    .st_collect = &intel_icx_cha_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
