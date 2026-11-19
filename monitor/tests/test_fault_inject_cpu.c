/* Fault coverage for cpu_counter_metrics.c (built with -DDEBUG in every build). Each backend
 * compiles one site: whatever the backend did not set this cycle is NOT_PRESENT. On x86 that is
 * every key when no core counter opened, else every key the publish left unset; on the aarch64 arm
 * backend every measured key without a core PMU. The backend reader and publish are stubbed so the
 * collect path runs without a core PMU. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "collect_tier.h"
#include "cpu_counter_metrics.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"

extern struct stats_type cpu_counter_metrics_stats_type;

struct stats_type *const stats_type_table[] = {&cpu_counter_metrics_stats_type};
const size_t stats_type_nr = 1;
int nr_cpus = 2;
processor_t processor;

#ifdef MONITOR_CPU_BACKEND_ARM
#include "arm_core_perf.h"
#include "cpu_counter_metrics_arm_publish.h"

int arm_core_perf_begin(int ncpus, const struct arm_core_perf_cfg *cfg)
{
  (void)ncpus;
  (void)cfg;
  return -1;
}

int arm_core_perf_ready(void)
{
  return 0;
}

void arm_core_perf_collect_cpu(int cpu, struct arm_core_counts *out)
{
  (void)cpu;
  (void)out;
}

void arm_core_perf_cleanup(void) {}

void arm_accumulate_from_util_sample(int i, const struct arm_cpu_sample *sample, long long delta_us)
{
  (void)i;
  (void)sample;
  (void)delta_us;
}

void publish_arm_cpu_stats(struct stats *stats, int cpu)
{
  (void)cpu;
  stats_set(stats, "cpu_clock_est_cycles", 1);
}

void publish_arm_measured_stats(struct stats *stats, const struct arm_core_counts *m)
{
  (void)stats;
  (void)m;
}

static void test_backend_marks_unset_keys(void)
{
  struct stats *cpu0;

  assert(cpu_counter_metrics_stats_type.st_begin(&cpu_counter_metrics_stats_type) == 0);
  stats_type_mark_not_updated(&cpu_counter_metrics_stats_type);
  cpu_counter_metrics_stats_type.st_collect(&cpu_counter_metrics_stats_type);
  cpu0 = get_current_stats(&cpu_counter_metrics_stats_type, "0");
  assert(cpu0->s_val_state[schema_ref(&cpu0->s_type->st_schema, "instr_retired")] ==
         STATS_SENTINEL_NOT_PRESENT);
  assert(cpu0->s_val_state[schema_ref(&cpu0->s_type->st_schema, "cpu_clock_est_cycles")] ==
         STATS_SENTINEL_OK);
}
#else
#include "cpu_counter_metrics_x86_publish.h"
#include "x86_core_perf.h"

static int g_x86_ready;

int x86_core_perf_begin(int ncpus, processor_t proc, const struct x86_core_perf_cfg *cfg)
{
  (void)ncpus;
  (void)proc;
  (void)cfg;
  return g_x86_ready ? 0 : -1;
}

int x86_core_perf_ready(void)
{
  return g_x86_ready;
}

void x86_core_perf_collect_cpu(int cpu, struct x86_core_counts *out)
{
  (void)cpu;
  memset(out, 0, sizeof(*out));
}

void x86_core_perf_cleanup(void) {}

void publish_x86_measured_stats(struct stats *stats, const struct x86_core_counts *c, int is_amd)
{
  (void)c;
  (void)is_amd;
  stats_set(stats, "instr_retired", 1);
}

static unsigned int state_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static void collect_cycle(void)
{
  stats_type_mark_not_updated(&cpu_counter_metrics_stats_type);
  cpu_counter_metrics_stats_type.st_collect(&cpu_counter_metrics_stats_type);
}

static void test_backend_marks_unset_keys(void)
{
  struct stats *cpu1;

  /* No core counter opened: every key of every CPU row is NOT_PRESENT. */
  g_x86_ready = 0;
  assert(cpu_counter_metrics_stats_type.st_begin(&cpu_counter_metrics_stats_type) == 0);
  collect_cycle();
  cpu1 = get_current_stats(&cpu_counter_metrics_stats_type, "1");
  for (size_t k = 0; k < cpu1->s_type->st_schema.sc_len; k++)
    assert(cpu1->s_val_state[k] == STATS_SENTINEL_NOT_PRESENT);

  /* Counters open: the published key keeps its value, the rest are NOT_PRESENT. */
  g_x86_ready = 1;
  assert(cpu_counter_metrics_stats_type.st_begin(&cpu_counter_metrics_stats_type) == 0);
  collect_cycle();
  assert(state_of(cpu1, "instr_retired") == STATS_SENTINEL_OK);
  assert(state_of(cpu1, "aperf") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(cpu1, "cpu_util_user_accum_us") == STATS_SENTINEL_NOT_PRESENT);
}
#endif

int main(void)
{
  assert(stats_type_init(&cpu_counter_metrics_stats_type) == 0);
  collect_tier_apply_to_type(&cpu_counter_metrics_stats_type);
  cpu_counter_metrics_stats_type.st_enabled = 1;
  test_backend_marks_unset_keys();
  stats_type_destroy(&cpu_counter_metrics_stats_type);
  printf("test_fault_inject_cpu passed\n");
  return 0;
}
