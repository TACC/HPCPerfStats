/* Fault coverage for cpu_counter_metrics.c (built with -DDEBUG in every build). Each backend
 * compiles one site: on LIKWID x86 a failed core-group prepare marks every CPU row with its code;
 * on the aarch64 arm backend whatever the backend did not set is NOT_PRESENT. The backend helpers
 * are stubbed so the collect path runs without LIKWID or a core PMU. */
#include <assert.h>
#include <stdint.h>
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
#include "cpu_counter_metrics_likwid_begin.h"
#include "likwid_pmc_adapter.h"

int cpu_counter_metrics_likwid_ready(void)
{
  return 1;
}

int likwid_backend_begin(struct stats_type *type)
{
  (void)type;
  return 0;
}

enum stats_sentinel likwid_pmc_adapter_prepare_collect(void)
{
  return STATS_SENTINEL_PMU_BUSY;
}

int likwid_pmc_adapter_read_cpu(struct stats *stats, int cpu, uint64_t *events, int nr_events,
                                int max_events)
{
  (void)stats;
  (void)cpu;
  (void)events;
  (void)nr_events;
  (void)max_events;
  return 0;
}

static void test_backend_marks_unset_keys(void)
{
  struct stats *cpu0;

  stats_type_mark_not_updated(&cpu_counter_metrics_stats_type);
  cpu_counter_metrics_stats_type.st_collect(&cpu_counter_metrics_stats_type);
  cpu0 = get_current_stats(&cpu_counter_metrics_stats_type, "1");
  for (size_t k = 0; k < cpu0->s_type->st_schema.sc_len; k++)
    assert(cpu0->s_val_state[k] == STATS_SENTINEL_PMU_BUSY);
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
