/*! \file intel_bdw_imc.c
 *  Intel Broadwell DRAM IMC via LIKWID uncore PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_snb_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_SNB_IMC_KEYS

static int intel_bdw_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_BDW);
}

static void intel_bdw_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_BDW);
}

struct stats_type intel_bdw_imc_stats_type = {
  .st_name = "intel_x86_uncore_imc_bdw",
  .st_begin = &intel_bdw_imc_begin,
  .st_collect = &intel_bdw_imc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
