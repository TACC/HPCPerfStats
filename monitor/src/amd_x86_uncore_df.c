/*! \file amd_x86_uncore_df.c
 *  AMD EPYC Data Fabric DRAM channels via LIKWID (family-named types).
 *  Legacy MSR amd_x86_uncore_df / amd64_df programming removed — no fallback.
 */

#include "stats.h"
#include "JOIN.h"
#include "amd_x86_uncore_df.h"
#include "likwid_uncore_adapter.h"

#define KEYS AMD_X86_UNCORE_DF_KEYS

static int amd_df_rome_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_DF_ROME);
}

static void amd_df_rome_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_DF_ROME);
}

static int amd_df_milan_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_DF_MILAN);
}

static void amd_df_milan_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_DF_MILAN);
}

static int amd_df_genoa_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_DF_GENOA);
}

static void amd_df_genoa_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_DF_GENOA);
}

static int amd_df_turin_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_DF_TURIN);
}

static void amd_df_turin_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_DF_TURIN);
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
