/*! \file intel_bdw_qpi.c
 *  Intel bdw QPI uncore via LIKWID PMON.
 */

#include "stats.h"
#include "JOIN.h"
#include "likwid_uncore_adapter.h"

#define KEYS \
  X(tx_l_flits_g1_snp, "E,W=48,U=flt", ""), \
  X(tx_l_flits_g1_hom, "E,W=48,U=flt", ""), \
  X(g1_drs_data, "E,W=48,U=flt", ""), \
  X(g2_ncb_data, "E,W=48,U=flt", "")

static int intel_bdw_qpi_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_QPI_BDW);
}

static void intel_bdw_qpi_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_QPI_BDW);
}

struct stats_type intel_bdw_qpi_stats_type = {
  .st_name = "intel_x86_uncore_qpi_bdw",
  .st_begin = &intel_bdw_qpi_begin,
  .st_collect = &intel_bdw_qpi_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
