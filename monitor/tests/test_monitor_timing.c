#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "monitor_timing.h"

static int nearly_equal(double a, double b)
{
  return fabs(a - b) < 1e-9;
}

static void test_next_boundary_basic_offsets(void)
{
  assert(nearly_equal(monitor_timing_next_boundary(299.9, 300.0), 300.0));
  assert(nearly_equal(monitor_timing_next_boundary(300.0, 300.0), 600.0));
  assert(nearly_equal(monitor_timing_next_boundary(300.1, 300.0), 600.0));
}

static void test_period_normalization(void)
{
  assert(nearly_equal(monitor_timing_normalize_period(0.0), 1.0));
  assert(nearly_equal(monitor_timing_normalize_period(-5.0), 1.0));
  assert(nearly_equal(monitor_timing_normalize_period(15.0), 15.0));
}

static void test_delayed_cycle_skips_to_next_valid_slot(void)
{
  double next = monitor_timing_next_boundary(913.0, 300.0);
  assert(nearly_equal(next, 1200.0));
}

/* GH200 #115: the sample callback fired d early, collected, and re-anchored
 * from a fresh wall clock while libev measured the delay from the stale loop
 * clock, so the timer re-fired a few ms later inside the same boundary. */
static void test_reanchor_early_fire_arms_next_boundary(void)
{
  const double period = 1.0;
  const double boundary = 300.0;
  const double d = 0.004;
  double loop_now = boundary - d;
  double served_at = boundary - d;
  double wall_now = boundary - 0.0005;
  double old_fire = loop_now + monitor_timing_next_boundary(wall_now, period) - wall_now;
  double new_fire = loop_now + monitor_timing_reanchor_wait(loop_now, wall_now, served_at, period);

  assert(old_fire < boundary + 0.5 * period);
  assert(nearly_equal(new_fire, boundary + period));
}

static void test_reanchor_early_fire_stale_loop_clock_compensated(void)
{
  const double period = 1.0;
  double loop_now = 300.001;
  double wall_now = 300.050;
  double fire = loop_now + monitor_timing_reanchor_wait(loop_now, wall_now, loop_now, period);

  assert(nearly_equal(fire, 301.0));
}

static void test_reanchor_early_fire_without_served_collect(void)
{
  assert(nearly_equal(monitor_timing_reanchor_wait(299.6, 299.6, -1.0, 1.0), 0.4));
  assert(nearly_equal(monitor_timing_reanchor_wait(NAN, 299.6, NAN, 1.0), 0.4));
  assert(monitor_timing_reanchor_wait(310.0, 299.6, -1.0, 1.0) > 0.0);
}

static void test_reanchor_early_fire_late_tick_keeps_next_boundary(void)
{
  double fire = 300.5 + monitor_timing_reanchor_wait(300.5, 300.6, 300.5, 1.0);

  assert(nearly_equal(fire, 301.0));
}

int main(void)
{
  test_next_boundary_basic_offsets();
  test_period_normalization();
  test_delayed_cycle_skips_to_next_valid_slot();
  test_reanchor_early_fire_arms_next_boundary();
  test_reanchor_early_fire_stale_loop_clock_compensated();
  test_reanchor_early_fire_without_served_collect();
  test_reanchor_early_fire_late_tick_keeps_next_boundary();
  printf("test_monitor_timing passed\n");
  return 0;
}
