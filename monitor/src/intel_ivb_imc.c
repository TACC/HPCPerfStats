/*! \file intel_ivb_imc.c
 *  Intel Ivy Bridge DRAM IMC via LIKWID uncore PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_snb_imc.h"
#include "likwid_uncore_adapter.h"

#define KEYS INTEL_SNB_IMC_KEYS

static int intel_ivb_imc_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_IMC_IVB);
}

static void intel_ivb_imc_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_IMC_IVB);
}

struct stats_type intel_ivb_imc_stats_type = {
  .st_name = "intel_x86_uncore_imc_ivb",
  .st_begin = &intel_ivb_imc_begin,
  .st_collect = &intel_ivb_imc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
