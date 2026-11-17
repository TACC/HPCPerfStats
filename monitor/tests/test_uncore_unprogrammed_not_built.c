/* Uncore IMC / AMD DF keys the programmed LIKWID eventset never counts emit NOT_BUILT on each
 * programmed device (computed once from the eventset); programmed keys keep their reading. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "amd_x86_uncore_df.h"
#include "cpu_counter_metrics_likwid_begin.h"
#include "intel_skx_imc.h"
#include "intel_spr_imc.h"
#include "likwid_uncore_adapter.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
processor_t processor = CASCADE_LAKE;

static int g_likwid_ready;

int cpu_counter_metrics_likwid_ready(void)
{
  return g_likwid_ready;
}

#ifdef HAVE_LIKWID
#include <math.h>

/* Fake perfmon group: one MBOX0 read counter whose setup / read / result the test chooses. */
static int g_fake_setup_rc;
static int g_fake_read_rc;
static double g_fake_result;

int perfmon_addEventSet(const char *events)
{
  (void)events;
  return 0;
}

int perfmon_setupCounters(int group)
{
  (void)group;
  return g_fake_setup_rc;
}

int perfmon_startCounters(void)
{
  return 0;
}

int perfmon_readGroupCounters(int group)
{
  (void)group;
  return g_fake_read_rc;
}

int perfmon_getNumberOfEvents(int group)
{
  (void)group;
  return 1;
}

char *perfmon_getCounterName(int group, int event)
{
  static char name[] = "MBOX0C0";

  (void)group;
  (void)event;
  return name;
}

double perfmon_getResult(int group, int event, int thread)
{
  (void)group;
  (void)event;
  (void)thread;
  return g_fake_result;
}

#endif

#define X SCHEMA_DEF
static const char g_skx_def[] = JOIN(INTEL_SKX_IMC_KEYS);
static const char g_spr_def[] = JOIN(INTEL_SPR_IMC_KEYS);
static const char g_df_def[] = JOIN(AMD_X86_UNCORE_DF_KEYS);
#undef X

static struct stats_type g_type;

static void init_type(const char *def)
{
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "uncore_tt");
  g_type.st_schema_def = (char *)def;
  assert(stats_type_init(&g_type) == 0);
}

static unsigned int state(const char *dev, const char *key)
{
  struct stats *row;
  int i = schema_ref(&g_type.st_schema, key);

  assert(dict_ref(&g_type.st_current_dict, dev) != NULL);
  row = get_current_stats(&g_type, dev);
  assert(i >= 0 && row != NULL);
  return row->s_val_state[i];
}

static void apply_plan(likwid_uncore_profile_t profile, const char *events)
{
  assert(likwid_uncore_adapter_plan(&g_type, profile, events) == 0);
  stats_type_mark_not_updated(&g_type);
  likwid_uncore_adapter_mark_unprogrammed(&g_type, profile);
}

static void test_skx_act_and_pre_keys_not_built(void)
{
  init_type(g_skx_def);
  apply_plan(LIKWID_UNCORE_PROFILE_IMC_SKX,
             likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SKX));
  for (int m = 0; m < 6; m++) {
    char dev[16];

    snprintf(dev, sizeof(dev), "mbox%d", m);
    assert(state(dev, "dram_act_count") == STATS_SENTINEL_NOT_BUILT);
    assert(state(dev, "dram_pre_count_miss") == STATS_SENTINEL_NOT_BUILT);
    assert(state(dev, "dram_cas_reads") == STATS_SENTINEL_NOT_UPDATED);
  }
  assert(dict_ref(&g_type.st_current_dict, "mbox6") == NULL);

  likwid_uncore_adapter_emit_counter(&g_type, LIKWID_UNCORE_PROFILE_IMC_SKX, "MBOX0C0", 11ULL);
  assert(state("mbox0", "dram_cas_reads") == STATS_SENTINEL_OK);
  assert(state("mbox0", "dram_act_count") == STATS_SENTINEL_NOT_BUILT);
  stats_type_destroy(&g_type);
}

static void test_spr_mbox_and_hbm_rows_split_keys(void)
{
  init_type(g_spr_def);
  apply_plan(LIKWID_UNCORE_PROFILE_IMC_SPR,
             likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_HBM));
  assert(state("mbox11", "hbm_cas_reads") == STATS_SENTINEL_NOT_BUILT);
  assert(state("mbox11", "hbm_cas_writes") == STATS_SENTINEL_NOT_BUILT);
  assert(state("mbox11", "dram_cas_writes") == STATS_SENTINEL_NOT_UPDATED);
  assert(state("hbm15", "dram_cas_reads") == STATS_SENTINEL_NOT_BUILT);
  assert(state("hbm15", "hbm_cas_reads") == STATS_SENTINEL_NOT_UPDATED);
  stats_type_destroy(&g_type);
}

static void test_rome_df_upper_channels_not_built(void)
{
  init_type(g_df_def);
  apply_plan(LIKWID_UNCORE_PROFILE_DF_ROME,
             likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_DF_ROME));
  assert(state("df", "dram_chan0_bytes") == STATS_SENTINEL_NOT_UPDATED);
  assert(state("df", "dram_chan1_bytes") == STATS_SENTINEL_NOT_UPDATED);
  assert(state("df", "dram_chan2_bytes") == STATS_SENTINEL_NOT_BUILT);
  assert(state("df", "dram_chan3_bytes") == STATS_SENTINEL_NOT_BUILT);
  stats_type_destroy(&g_type);

  init_type(g_df_def);
  apply_plan(LIKWID_UNCORE_PROFILE_DF_MILAN,
             likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_DF_MILAN));
  assert(state("df", "dram_chan3_bytes") == STATS_SENTINEL_NOT_UPDATED);
  stats_type_destroy(&g_type);
}

static void test_plan_rejects_bad_input(void)
{
  init_type(g_df_def);
  assert(likwid_uncore_adapter_plan(&g_type, LIKWID_UNCORE_PROFILE_DF_GENOA, NULL) < 0);
  assert(likwid_uncore_adapter_plan(&g_type, LIKWID_UNCORE_PROFILE_DF_GENOA, "BOGUS:XYZ0") < 0);
  assert(likwid_uncore_adapter_plan(NULL, LIKWID_UNCORE_PROFILE_DF_GENOA, "X:DFC0") < 0);
  /* An unplanned profile marks nothing and creates no row. */
  likwid_uncore_adapter_mark_unprogrammed(&g_type, LIKWID_UNCORE_PROFILE_DF_GENOA);
  assert(g_type.st_current_dict.d_count == 0);
  stats_type_destroy(&g_type);
}

#ifdef HAVE_LIKWID
/* A failed group setup or read marks every planned key still unset BACKEND_ERROR (NOT_BUILT stays);
 * an unusable result is INVALID_VALUE on its own key only. */
static void test_collect_failure_codes(void)
{
  init_type(g_skx_def);
  g_likwid_ready = 1;
  assert(likwid_uncore_adapter_begin(&g_type, LIKWID_UNCORE_PROFILE_IMC_SKX) == 0);

  g_fake_setup_rc = -1;
  stats_type_mark_not_updated(&g_type);
  likwid_uncore_adapter_collect(&g_type, LIKWID_UNCORE_PROFILE_IMC_SKX);
  assert(state("mbox0", "dram_cas_reads") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state("mbox0", "dram_act_count") == STATS_SENTINEL_NOT_BUILT);

  g_fake_setup_rc = 0;
  g_fake_read_rc = -1;
  stats_type_mark_not_updated(&g_type);
  likwid_uncore_adapter_collect(&g_type, LIKWID_UNCORE_PROFILE_IMC_SKX);
  assert(state("mbox3", "dram_cas_writes") == STATS_SENTINEL_BACKEND_ERROR);

  g_fake_read_rc = 0;
  g_fake_result = NAN;
  stats_type_mark_not_updated(&g_type);
  likwid_uncore_adapter_collect(&g_type, LIKWID_UNCORE_PROFILE_IMC_SKX);
  assert(state("mbox0", "dram_cas_reads") == STATS_SENTINEL_INVALID_VALUE);
  assert(state("mbox1", "dram_cas_reads") == STATS_SENTINEL_NOT_UPDATED);

  g_fake_result = 12.0;
  stats_type_mark_not_updated(&g_type);
  likwid_uncore_adapter_collect(&g_type, LIKWID_UNCORE_PROFILE_IMC_SKX);
  assert(state("mbox0", "dram_cas_reads") == STATS_SENTINEL_OK);
  g_likwid_ready = 0;
  stats_type_destroy(&g_type);
}
#endif

int main(void)
{
  test_skx_act_and_pre_keys_not_built();
  test_spr_mbox_and_hbm_rows_split_keys();
  test_rome_df_upper_channels_not_built();
  test_plan_rejects_bad_input();
#ifdef HAVE_LIKWID
  test_collect_failure_codes();
#endif
  printf("test_uncore_unprogrammed_not_built passed\n");
  return 0;
}
