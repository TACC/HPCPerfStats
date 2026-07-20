/*! \file intel_icx_imc.c
 *  Intel Ice Lake server DRAM IMC via LIKWID uncore PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_icx_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_ICX_IMC_KEYS

static int intel_icx_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_ICX);
}

static void intel_icx_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_ICX);
}

struct stats_type intel_icx_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_icx",
    .st_begin = &intel_icx_imc_begin,
    .st_collect = &intel_icx_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
