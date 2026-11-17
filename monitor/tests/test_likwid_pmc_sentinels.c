/* host_cpu_hw x86 LIKWID value sentinels.
 *
 * Pure map: keys the eventset does not program are NOT_BUILT when they apply to this CPU (Intel
 * fp_arith_inst_retired_*) and NOT_PRESENT when they belong to another vendor
 * or architecture. Adapter (fake LIKWID): unprogrammed codes every tick,
 * readGroupCounters failure BACKEND_ERROR, setupCounters -EBUSY PMU_BUSY, unusable
 * result INVALID_VALUE. */
#include <assert.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "collect_tier.h"
#include "cpu_counter_metrics.h"
#include "likwid_arch_map.h"
#include "likwid_pmc_adapter.h"
#include "likwid_pmc_schema_map.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

#define X SCHEMA_DEF
static char g_schema_def[] = JOIN(CPU_COUNTER_METRICS_KEYS);
#undef X

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
processor_t processor = SAPPHIRE_RAPIDS;
static struct stats_type g_type;

static unsigned int code_of(const unsigned char *codes, const char *key)
{
  int i = schema_ref(&g_type.st_schema, key);

  assert(i >= 0);
  return codes[i];
}

static void test_intel_key_codes(void)
{
  unsigned char codes[128];

  assert(g_type.st_schema.sc_len <= sizeof(codes));
  assert(likwid_pmc_key_codes(&g_type.st_schema,
                              likwid_arch_eventset_for_processor(SAPPHIRE_RAPIDS, 8), 0,
                              codes) == 0);
  assert(code_of(codes, "instr_retired") == STATS_SENTINEL_OK);
  assert(code_of(codes, "aperf") == STATS_SENTINEL_OK);
  assert(code_of(codes, "mperf") == STATS_SENTINEL_OK);
  assert(code_of(codes, "cycles_unhalted_core") == STATS_SENTINEL_OK);
  assert(code_of(codes, "l1d_replacement") == STATS_SENTINEL_OK);
  /* Applies to Intel, not programmed by this build's eventset. */
  assert(code_of(codes, "fp_arith_inst_retired_scalar_double") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of(codes, "fp_arith_inst_retired_512b_packed_single") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of(codes, "mem_load_uops_retired_llc_hit") == STATS_SENTINEL_NOT_BUILT);
  /* Foreign vendor / architecture. */
  assert(code_of(codes, "retired_instructions") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of(codes, "ls_dispatch") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of(codes, "arm_est_flops") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of(codes, "cpu_util_user_accum_us") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of(codes, "dram_chan0_bytes") == STATS_SENTINEL_NOT_PRESENT);
  assert(likwid_pmc_key_codes(NULL, "X:PMC0", 0, codes) == -1);
}

static void test_amd_key_codes(void)
{
  unsigned char codes[128];

  assert(likwid_pmc_key_codes(&g_type.st_schema, likwid_arch_eventset_for_processor(AMD_GENOA, 6),
                              1, codes) == 0);
  assert(code_of(codes, "retired_instructions") == STATS_SENTINEL_OK);
  assert(code_of(codes, "instr_retired") == STATS_SENTINEL_OK);
  assert(code_of(codes, "ls_dispatch") == STATS_SENTINEL_OK);
  assert(code_of(codes, "fp_arith_inst_retired_scalar_double") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of(codes, "instr_retired_any") == STATS_SENTINEL_NOT_PRESENT);
  /* No FIXC / cycle event on AMD: applies, not programmed. */
  assert(code_of(codes, "aperf") == STATS_SENTINEL_NOT_BUILT);
  assert(likwid_pmc_unprogrammed_code(NULL, 1) == STATS_SENTINEL_NOT_PRESENT);
}

#ifdef HAVE_LIKWID
#include <likwid.h>

/* Fake LIKWID: one group of the SPR eventset's first three fixed events. */
static int g_setup_rc;
static int g_read_rc;
static double g_results[3];
static char g_counter_names[3][8] = {"FIXC0", "FIXC1", "FIXC2"};
static char g_event_names[3][32] = {"INSTR_RETIRED_ANY", "CPU_CLK_UNHALTED_CORE",
                                    "CPU_CLK_UNHALTED_REF"};

int topology_init(void)
{
  return 0;
}
int numa_init(void)
{
  return 0;
}
void perfmon_setVerbosity(int verbose)
{
  (void)verbose;
}
void HPMmode(int mode)
{
  (void)mode;
}
int HPMinit(void)
{
  return 0;
}
void HPMfinalize(void) {}
int perfmon_init(int nrThreads, const int *threadsToCpu)
{
  (void)nrThreads;
  (void)threadsToCpu;
  return 0;
}
void perfmon_finalize(void) {}
int perfmon_addEventSet(const char *eventCString)
{
  (void)eventCString;
  return 0;
}
int perfmon_setupCounters(int groupId)
{
  (void)groupId;
  return g_setup_rc;
}
int perfmon_startCounters(void)
{
  return 0;
}
int perfmon_readGroupCounters(int groupId)
{
  (void)groupId;
  return g_read_rc;
}
int perfmon_getNumberOfEvents(int groupId)
{
  (void)groupId;
  return 3;
}
char *perfmon_getCounterName(int groupId, int eventId)
{
  (void)groupId;
  return g_counter_names[eventId];
}
char *perfmon_getEventName(int groupId, int eventId)
{
  (void)groupId;
  return g_event_names[eventId];
}
double perfmon_getResult(int groupId, int eventId, int threadId)
{
  (void)groupId;
  (void)threadId;
  return g_results[eventId];
}

static unsigned int state_of(struct stats *s, const char *key)
{
  return s->s_val_state[schema_ref(&g_type.st_schema, key)];
}

static void test_adapter_read_codes(void)
{
  struct stats *s = get_current_stats(&g_type, "0");
  uint64_t ctls[8] = {0};

  assert(likwid_pmc_adapter_init(1) == 0);
  g_setup_rc = 0;
  assert(likwid_pmc_adapter_setup_events(likwid_arch_eventset_for_processor(SAPPHIRE_RAPIDS, 8)) ==
         0);

  /* Healthy tick: values OK, unprogrammed fp_arith NOT_BUILT, foreign keys NOT_PRESENT. */
  g_read_rc = 0;
  g_results[0] = 100.0;
  g_results[1] = 200.0;
  g_results[2] = NAN;
  stats_type_mark_not_updated(&g_type);
  assert(likwid_pmc_adapter_prepare_collect() == STATS_SENTINEL_OK);
  assert(likwid_pmc_adapter_read_cpu(s, 0, ctls, 8, 8) == 0);
  assert(state_of(s, "instr_retired") == STATS_SENTINEL_OK);
  assert(s->s_val[schema_ref(&g_type.st_schema, "instr_retired")] == 100ULL);
  assert(state_of(s, "aperf") == STATS_SENTINEL_OK);
  assert(state_of(s, "mperf") == STATS_SENTINEL_INVALID_VALUE);
  assert(state_of(s, "cycles_unhalted_ref") == STATS_SENTINEL_INVALID_VALUE);
  assert(state_of(s, "fp_arith_inst_retired_scalar_double") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "retired_instructions") == STATS_SENTINEL_NOT_PRESENT);

  /* Every tick, not only the first: no stored 0 behind fp_arith. */
  stats_type_mark_not_updated(&g_type);
  assert(likwid_pmc_adapter_read_cpu(s, 0, ctls, 8, 8) == 0);
  assert(state_of(s, "fp_arith_inst_retired_256b_packed_double") == STATS_SENTINEL_NOT_BUILT);
  assert(s->s_val[schema_ref(&g_type.st_schema, "fp_arith_inst_retired_256b_packed_double")] ==
         0ULL);

  /* readGroupCounters fails: programmed keys BACKEND_ERROR, static codes kept. */
  g_read_rc = -1;
  stats_type_mark_not_updated(&g_type);
  assert(likwid_pmc_adapter_read_cpu(s, 0, ctls, 8, 8) == -1);
  assert(state_of(s, "instr_retired") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state_of(s, "fp_arith_inst_retired_scalar_single") == STATS_SENTINEL_NOT_BUILT);

  /* Counter register in use (setupCounters -EBUSY) vs any other setup failure. */
  g_setup_rc = -EBUSY;
  assert(likwid_pmc_adapter_prepare_collect() == STATS_SENTINEL_PMU_BUSY);
  g_setup_rc = -EINVAL;
  assert(likwid_pmc_adapter_prepare_collect() == STATS_SENTINEL_BACKEND_ERROR);
  likwid_pmc_adapter_finalize();
  assert(likwid_pmc_adapter_prepare_collect() == STATS_SENTINEL_BACKEND_ERROR);
}
#endif

int main(void)
{
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", CPU_COUNTER_METRICS_ST_NAME);
  g_type.st_schema_def = g_schema_def;
  assert(stats_type_init(&g_type) == 0);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);

  test_intel_key_codes();
  test_amd_key_codes();
#ifdef HAVE_LIKWID
  test_adapter_read_codes();
#endif
  stats_type_destroy(&g_type);
  printf("test_likwid_pmc_sentinels passed\n");
  return 0;
}
