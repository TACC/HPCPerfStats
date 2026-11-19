/*! \file intel_srf_imc.c
 *  Intel Sierra Forest SCH0 IMC DRAM CAS via direct perf on the uncore PMUs (x86_uncore_perf.c).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_srf_imc.h"
#include "intel_processor.h"
#include "x86_uncore_perf.h"

#define KEYS INTEL_SRF_IMC_KEYS

static struct x86_uncore_perf g_intel_srf_imc = X86_UNCORE_PERF_INIT(&x86_uncore_profile_imc_gnr);

static int intel_srf_imc_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_intel_srf_imc, intel_processor_is_srf(processor));
}

static void intel_srf_imc_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_intel_srf_imc);
}

struct stats_type intel_srf_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_srf",
    .st_begin = &intel_srf_imc_begin,
    .st_collect = &intel_srf_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
