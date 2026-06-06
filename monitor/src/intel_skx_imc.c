/*! \file intel_skx_imc.c
 *  Intel Skylake-X / Cascade Lake DRAM IMC via LIKWID uncore PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_skx_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_SKX_IMC_KEYS

static int intel_skx_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_SKX);
}

static void intel_skx_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_SKX);
}

struct stats_type intel_skx_imc_stats_type = {
  .st_name = "intel_x86_uncore_imc_skx",
  .st_begin = &intel_skx_imc_begin,
  .st_collect = &intel_skx_imc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
