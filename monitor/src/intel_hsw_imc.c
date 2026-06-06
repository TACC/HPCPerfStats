/*! \file intel_hsw_imc.c
 *  Intel Haswell DRAM IMC via LIKWID uncore PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_snb_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_SNB_IMC_KEYS

static int intel_hsw_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_HSW);
}

static void intel_hsw_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_HSW);
}

struct stats_type intel_hsw_imc_stats_type = {
  .st_name = "intel_x86_uncore_imc_hsw",
  .st_begin = &intel_hsw_imc_begin,
  .st_collect = &intel_hsw_imc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
