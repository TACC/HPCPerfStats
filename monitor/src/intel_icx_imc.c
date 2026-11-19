/*! \file intel_icx_imc.c
 *  Intel Ice Lake-SP IMC DRAM CAS via direct perf on the uncore PMUs (x86_uncore_perf.c).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_icx_imc.h"
#include "intel_processor.h"
#include "x86_uncore_perf.h"

#define KEYS INTEL_ICX_IMC_KEYS

static struct x86_uncore_perf g_intel_icx_imc = X86_UNCORE_PERF_INIT(&x86_uncore_profile_imc_icx);

static int intel_icx_imc_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_intel_icx_imc, intel_processor_is_icx(processor));
}

static void intel_icx_imc_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_intel_icx_imc);
}

struct stats_type intel_icx_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_icx",
    .st_begin = &intel_icx_imc_begin,
    .st_collect = &intel_icx_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
