#include "stats.h"
#include "JOIN.h"
#include "likwid_uncore_adapter.h"

#define KEYS \
  X(requests_reads, "E,W=48", ""), \
  X(requests_writes, "E,W=48", ""), \
  X(clockticks, "E,W=48", ""), \
  X(imc_writes, "E,W=48", "")

static int intel_bdw_hau_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_HAU_BDW);
}

static void intel_bdw_hau_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_HAU_BDW);
}

struct stats_type intel_bdw_hau_stats_type = {
  .st_name = "intel_x86_uncore_hau_bdw",
  .st_begin = &intel_bdw_hau_begin,
  .st_collect = &intel_bdw_hau_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
