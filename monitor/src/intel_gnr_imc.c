/*! \file intel_gnr_imc.c
 *  Intel Granite Rapids SCH0 IMC DRAM CAS via direct perf on the uncore PMUs (x86_uncore_perf.c).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_gnr_imc.h"
#include "intel_processor.h"
#include "x86_uncore_perf.h"

#define KEYS INTEL_GNR_IMC_KEYS

static struct x86_uncore_perf g_intel_gnr_imc = X86_UNCORE_PERF_INIT(&x86_uncore_profile_imc_gnr);

static int intel_gnr_imc_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_intel_gnr_imc, intel_processor_is_gnr(processor));
}

static void intel_gnr_imc_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_intel_gnr_imc);
}

struct stats_type intel_gnr_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_gnr",
    .st_begin = &intel_gnr_imc_begin,
    .st_collect = &intel_gnr_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
