/*! \file intel_gnr_imc.c
 *  Intel Granite Rapids DRAM IMC via LIKWID uncore PMON (CAS_COUNT_SCH0_*).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_gnr_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_GNR_IMC_KEYS

static int intel_gnr_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_GNR);
}

static void intel_gnr_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_GNR);
}

struct stats_type intel_gnr_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_gnr",
    .st_begin = &intel_gnr_imc_begin,
    .st_collect = &intel_gnr_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
