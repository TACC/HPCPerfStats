/* Slow-boundary math (monitor_timing) and collect-phase gating (collect_tier). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "collect.h"
#include "collect_tier.h"
#include "monitor_timing.h"
#include "stats.h"
#include "stats_runtime.h"

void cpu_stats_invalidate_file_caches(void) {}
void net_stats_invalidate_iface_cache(void) {}
void auto_disable_optional_stats_by_lspci(void) {}
void metric_profiler_collect_begin(const char *name) { (void) name; }
void metric_profiler_collect_end(const char *name) { (void) name; }
void metric_profiler_cycle_begin(void) {}
void metric_profiler_cycle_end(FILE *stream) { (void) stream; }
void monitor_log_error(const char *fmt, ...) { (void) fmt; }
void monitor_log_warn(const char *fmt, ...) { (void) fmt; }
void collect_set_key_active_hook(collect_key_active_fn fn, void *ctx)
{
  (void) fn;
  (void) ctx;
}

int stats_type_init(struct stats_type *type)
{
  (void) type;
  return 0;
}

void stats_type_destroy(struct stats_type *type) { (void) type; }

struct stats_type *stats_type_for_each(size_t *i)
{
  (void) i;
  return NULL;
}

struct stats_type *stats_type_get(const char *name)
{
  (void) name;
  return NULL;
}

static void test_slow_slot_math(void)
{
  assert(monitor_collect_slow_slot(0.0, 600.0) == 0);
  assert(monitor_collect_slow_slot(599.0, 600.0) == 0);
  assert(monitor_collect_slow_slot(600.0, 600.0) == 1);
  assert(monitor_collect_slow_slot(1234.0, 600.0) == 2);
  /* Negative / non-finite normalized to slot 0. */
  assert(monitor_collect_slow_slot(-5.0, 600.0) == 0);
}

static void test_should_run_slow(void)
{
  /* First tick (last slot < 0) always runs slow. */
  assert(monitor_collect_should_run_slow(0.0, -1, 600.0) != 0);
  /* Same slot -> no slow run. */
  assert(monitor_collect_should_run_slow(30.0, 0, 600.0) == 0);
  assert(monitor_collect_should_run_slow(599.0, 0, 600.0) == 0);
  /* Slot advanced -> slow run. */
  assert(monitor_collect_should_run_slow(600.0, 0, 600.0) != 0);
}

/* At sample_freq=30 / sample_freq_slow=600, slow fires every 20th fast tick. */
static void test_alignment_with_fast_ticks(void)
{
  long long last_slot = -1;
  int slow_runs = 0;
  int tick;

  for (tick = 0; tick < 40; tick++) {
    double now = tick * 30.0;
    if (monitor_collect_should_run_slow(now, last_slot, 600.0)) {
      slow_runs++;
      last_slot = monitor_collect_slow_slot(now, 600.0);
    }
  }
  /* ticks 0 and 20 cross slow boundaries (slots 0 and 1) within 40 ticks. */
  assert(slow_runs == 2);
}

static void test_phase_get_set_and_effective(void)
{
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(collect_tier_get_phase() == COLLECT_FAST_ONLY);
  /* write_hdr forces FULL regardless of current phase. */
  assert(collect_tier_effective_phase(1) == COLLECT_FULL);
  assert(collect_tier_effective_phase(0) == COLLECT_FAST_ONLY);

  collect_tier_set_phase(COLLECT_FULL);
  assert(collect_tier_effective_phase(0) == COLLECT_FULL);
}

static void test_runtime_collect_phase_for_tick(void)
{
  long long last_slot = -1;
  enum collect_phase phase;

  collect_tier_set_enabled(1);
  phase = stats_runtime_collect_phase_for_tick(0.0, &last_slot, 600.0);
  assert(phase == COLLECT_FULL);
  assert(last_slot == 0);
  assert(collect_tier_get_phase() == COLLECT_FULL);

  phase = stats_runtime_collect_phase_for_tick(30.0, &last_slot, 600.0);
  assert(phase == COLLECT_FAST_ONLY);
  assert(last_slot == 0);
  assert(collect_tier_get_phase() == COLLECT_FAST_ONLY);

  phase = stats_runtime_collect_phase_for_tick(599.0, &last_slot, 600.0);
  assert(phase == COLLECT_FAST_ONLY);
  assert(last_slot == 0);

  phase = stats_runtime_collect_phase_for_tick(600.0, &last_slot, 600.0);
  assert(phase == COLLECT_FULL);
  assert(last_slot == 1);
  assert(collect_tier_get_phase() == COLLECT_FULL);
}

static void test_tier_disabled_stays_full(void)
{
  long long last_slot = 5;

  collect_tier_set_enabled(0);
  assert(stats_runtime_collect_phase_for_tick(30.0, &last_slot, 600.0)
         == COLLECT_FULL);
  assert(collect_tier_get_phase() == COLLECT_FULL);
  assert(last_slot == 5);
  collect_tier_set_enabled(1);
}

int main(void)
{
  test_slow_slot_math();
  test_should_run_slow();
  test_alignment_with_fast_ticks();
  test_phase_get_set_and_effective();
  test_runtime_collect_phase_for_tick();
  test_tier_disabled_stays_full();
  printf("test_collect_phase passed\n");
  return 0;
}
