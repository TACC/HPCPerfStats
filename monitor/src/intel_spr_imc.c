/*! \file intel_spr_imc.c
 *  Intel Sapphire Rapids DDR (uncore_imc) and HBM (uncore_hbm) IMC DRAM CAS via direct perf on the uncore PMUs (x86_uncore_perf.c).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_spr_imc.h"
#include "intel_processor.h"
#include "x86_uncore_perf.h"

#define KEYS INTEL_SPR_IMC_KEYS

static struct x86_uncore_perf g_intel_spr_imc = X86_UNCORE_PERF_INIT(&x86_uncore_profile_imc_spr);

static int intel_spr_imc_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_intel_spr_imc, intel_processor_is_spr(processor));
}

static void intel_spr_imc_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_intel_spr_imc);
}

struct stats_type intel_spr_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_spr",
    .st_begin = &intel_spr_imc_begin,
    .st_collect = &intel_spr_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
