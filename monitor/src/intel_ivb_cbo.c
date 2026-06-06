/*! \file intel_ivb_cbo.c
 *  Intel IVB CBo uncore via LIKWID PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "likwid_uncore_adapter.h"

#define KEYS \
  X(llc_lookup_data_read, "E,W=44", ""), \
  X(llc_lookup_write, "E,W=44", ""), \
  X(ring_iv_used, "E,W=44", ""), \
  X(counter0_occupancy, "E,W=44", "")

static int intel_ivb_cbo_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_CBO_IVB);
}

static void intel_ivb_cbo_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_CBO_IVB);
}

struct stats_type intel_ivb_cbo_stats_type = {
  .st_name = "intel_x86_uncore_cbo_ivb",
  .st_begin = &intel_ivb_cbo_begin,
  .st_collect = &intel_ivb_cbo_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
