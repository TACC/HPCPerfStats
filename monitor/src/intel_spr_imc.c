/*! \file intel_spr_imc.c
 *  Intel Sapphire Rapids DDR and HBM IMC via LIKWID uncore PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_spr_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_SPR_IMC_KEYS

static int intel_spr_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_SPR);
}

static void intel_spr_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_SPR);
}

struct stats_type intel_spr_imc_stats_type = {
  .st_name = "intel_x86_uncore_imc_spr",
  .st_begin = &intel_spr_imc_begin,
  .st_collect = &intel_spr_imc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
