/* Pure helpers: first-fail latch and hourly RMQ failure deltas (release logging). */
#include <assert.h>
#include <stdio.h>

#include "monitor_release_log.h"

static void test_should_emit_always_when_not_first_only(void)
{
  int latched = 0;

  assert(monitor_release_log_should_emit(&latched, 0) == 1);
  assert(latched == 0);
  assert(monitor_release_log_should_emit(&latched, 0) == 1);
  assert(latched == 0);
}

static void test_should_emit_first_only_latches(void)
{
  int latched = 0;

  assert(monitor_release_log_should_emit(&latched, 1) == 1);
  assert(latched == 1);
  assert(monitor_release_log_should_emit(&latched, 1) == 0);
  assert(latched == 1);
  monitor_release_log_clear_latch(&latched);
  assert(latched == 0);
  assert(monitor_release_log_should_emit(&latched, 1) == 1);
}

static void test_should_emit_null_latched(void)
{
  assert(monitor_release_log_should_emit(NULL, 1) == 1);
  assert(monitor_release_log_should_emit(NULL, 0) == 1);
  monitor_release_log_clear_latch(NULL);
}

static void test_failure_deltas(void)
{
  unsigned long d_c = 0, d_q = 0, d_p = 0;

  monitor_release_log_failure_deltas(1, 2, 3, 5, 7, 10, &d_c, &d_q, &d_p);
  assert(d_c == 4);
  assert(d_q == 5);
  assert(d_p == 7);

  monitor_release_log_failure_deltas(0, 0, 0, 0, 0, 0, &d_c, &d_q, &d_p);
  assert(d_c == 0 && d_q == 0 && d_p == 0);

  monitor_release_log_failure_deltas(1, 2, 3, 5, 7, 10, NULL, NULL, NULL);
}

int main(void)
{
  test_should_emit_always_when_not_first_only();
  test_should_emit_first_only_latches();
  test_should_emit_null_latched();
  test_failure_deltas();
  printf("test_monitor_release_logging passed\n");
  return 0;
}
