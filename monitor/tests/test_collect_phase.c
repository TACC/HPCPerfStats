/* Slow-boundary math (monitor_timing) and collect-phase gating (collect_tier). */
#include <assert.h>
#include <stdio.h>

#include "collect_tier.h"
#include "monitor_timing.h"

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

int main(void)
{
  test_slow_slot_math();
  test_should_run_slow();
  test_alignment_with_fast_ticks();
  test_phase_get_set_and_effective();
  printf("test_collect_phase passed\n");
  return 0;
}
