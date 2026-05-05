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

static void test_seconds_until_next_boundary(void)
{
  double wait = monitor_timing_seconds_until_next_boundary(300.0, 300.0);
  assert(wait > 0.0);
  assert(wait <= 300.0);
  assert(nearly_equal(monitor_timing_seconds_until_next_boundary(299.5, 300.0), 0.5));
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

int main(void)
{
  test_next_boundary_basic_offsets();
  test_seconds_until_next_boundary();
  test_period_normalization();
  test_delayed_cycle_skips_to_next_valid_slot();
  printf("test_monitor_timing passed\n");
  return 0;
}
