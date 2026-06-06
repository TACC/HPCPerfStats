/*! \file intel_skx_cha.c
 *  Intel Skylake-X / Cascade Lake CHA uncore via LIKWID PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "likwid_uncore_adapter.h"

#define KEYS \
  X(sf_evictions_mes, "E,W=48", ""), \
  X(llc_lookup_data_read_local, "E,W=48", ""), \
  X(bypass_cha_imc_all, "E,W=48", ""), \
  X(llc_lookup_write, "E,W=48", "")

static int intel_skx_cha_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_CHA_SKX);
}

static void intel_skx_cha_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_CHA_SKX);
}

struct stats_type intel_skx_cha_stats_type = {
  .st_name = "intel_x86_uncore_cha_skx",
  .st_begin = &intel_skx_cha_begin,
  .st_collect = &intel_skx_cha_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
