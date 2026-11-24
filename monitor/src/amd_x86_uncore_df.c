/*! \file amd_x86_uncore_df.c
 *  AMD EPYC Data Fabric DRAM channels via direct perf on the amd_df PMU (x86_uncore_perf.c),
 *  one stats type per family. No MSR fallback.
 */

#include "stats.h"
#include "JOIN.h"
#include "amd_processor.h"
#include "amd_x86_uncore_df.h"
#include "x86_uncore_perf.h"

#define KEYS AMD_X86_UNCORE_DF_KEYS

static struct x86_uncore_perf g_amd_df_rome = X86_UNCORE_PERF_INIT(&x86_uncore_profile_df_zen2);

static int amd_df_rome_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_amd_df_rome, amd_processor_is_rome(processor));
}

static void amd_df_rome_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_amd_df_rome);
}

static struct x86_uncore_perf g_amd_df_milan = X86_UNCORE_PERF_INIT(&x86_uncore_profile_df_zen3);

static int amd_df_milan_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_amd_df_milan, amd_processor_is_milan(processor));
}

static void amd_df_milan_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_amd_df_milan);
}

static struct x86_uncore_perf g_amd_df_genoa = X86_UNCORE_PERF_INIT(&x86_uncore_profile_df_zen5);

static int amd_df_genoa_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_amd_df_genoa, amd_processor_is_genoa(processor));
}

static void amd_df_genoa_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_amd_df_genoa);
}

static struct x86_uncore_perf g_amd_df_turin = X86_UNCORE_PERF_INIT(&x86_uncore_profile_df_zen5);

static int amd_df_turin_begin(struct stats_type *type)
{
  return x86_uncore_perf_begin(type, &g_amd_df_turin, amd_processor_is_turin(processor));
}

static void amd_df_turin_collect(struct stats_type *type)
{
  x86_uncore_perf_collect(type, &g_amd_df_turin);
}

struct stats_type amd_x86_uncore_df_rome_stats_type = {
    .st_name = "amd_x86_uncore_df_rome",
    .st_begin = &amd_df_rome_begin,
    .st_collect = &amd_df_rome_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};

struct stats_type amd_x86_uncore_df_milan_stats_type = {
    .st_name = "amd_x86_uncore_df_milan",
    .st_begin = &amd_df_milan_begin,
    .st_collect = &amd_df_milan_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};

struct stats_type amd_x86_uncore_df_genoa_stats_type = {
    .st_name = "amd_x86_uncore_df_genoa",
    .st_begin = &amd_df_genoa_begin,
    .st_collect = &amd_df_genoa_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};

struct stats_type amd_x86_uncore_df_turin_stats_type = {
    .st_name = "amd_x86_uncore_df_turin",
    .st_begin = &amd_df_turin_begin,
    .st_collect = &amd_df_turin_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
