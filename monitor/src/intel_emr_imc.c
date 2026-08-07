/*! \file intel_emr_imc.c
 *  Intel Emerald Rapids DDR and HBM IMC via LIKWID uncore PMON (SPR event ladder).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_emr_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_EMR_IMC_KEYS

static int intel_emr_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_EMR);
}

static void intel_emr_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_EMR);
}

struct stats_type intel_emr_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_emr",
    .st_begin = &intel_emr_imc_begin,
    .st_collect = &intel_emr_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
