/*! \file intel_srf_imc.c
 *  Intel Sierra Forest DRAM IMC via LIKWID uncore PMON (CAS_COUNT_SCH0_*).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_srf_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_SRF_IMC_KEYS

static int intel_srf_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_SRF);
}

static void intel_srf_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_SRF);
}

struct stats_type intel_srf_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_srf",
    .st_begin = &intel_srf_imc_begin,
    .st_collect = &intel_srf_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
