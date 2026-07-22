/* Pure helpers: first-fail latch, named failure counters, stderr quiet (release logging). */
#include <assert.h>
#include <stdio.h>
#include <unistd.h>

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

static void test_named_fail_note_and_clear(void)
{
  unsigned long ring = 0, ib = 0, nv = 0;

  monitor_release_fail_reset_for_test();
  assert(monitor_release_fail_note(MONITOR_REL_FAIL_RING_RESEND, 1) == 1);
  assert(monitor_release_fail_count(MONITOR_REL_FAIL_RING_RESEND) == 1);
  assert(monitor_release_fail_note(MONITOR_REL_FAIL_RING_RESEND, 1) == 0);
  assert(monitor_release_fail_count(MONITOR_REL_FAIL_RING_RESEND) == 2);
  monitor_release_fail_clear(MONITOR_REL_FAIL_RING_RESEND);
  assert(monitor_release_fail_note(MONITOR_REL_FAIL_RING_RESEND, 1) == 1);
  assert(monitor_release_fail_count(MONITOR_REL_FAIL_RING_RESEND) == 3);

  assert(monitor_release_fail_note(MONITOR_REL_FAIL_IB_MAD, 0) == 1);
  assert(monitor_release_fail_note(MONITOR_REL_FAIL_IB_MAD, 0) == 1);
  assert(monitor_release_fail_count(MONITOR_REL_FAIL_IB_MAD) == 2);

  assert(monitor_release_fail_note(MONITOR_REL_FAIL_NVIDIA_ZERO, 1) == 1);
  assert(monitor_release_fail_note(MONITOR_REL_FAIL_NVIDIA_ZERO, 1) == 0);

  monitor_release_fail_get_counts(&ring, &ib, &nv);
  assert(ring == 3);
  assert(ib == 2);
  assert(nv == 2);

  monitor_release_fail_get_counts(NULL, NULL, NULL);
  assert(monitor_release_fail_count((monitor_rel_fail_id)99) == 0);
  assert(monitor_release_fail_note((monitor_rel_fail_id)99, 1) == 1);
  monitor_release_fail_clear((monitor_rel_fail_id)99);

  monitor_release_fail_reset_for_test();
  assert(monitor_release_fail_count(MONITOR_REL_FAIL_RING_RESEND) == 0);
}

static void test_stderr_quiet_begin_end(void)
{
  int saved = -1;
  int null_fd = -1;

  monitor_stderr_quiet_begin(NULL, &null_fd);
  monitor_stderr_quiet_begin(&saved, NULL);
  monitor_stderr_quiet_begin(&saved, &null_fd);
  assert(saved >= 0);
  /* Writing to stderr while quieted must not fail the process. */
  (void)write(STDERR_FILENO, "x", 1);
  monitor_stderr_quiet_end(&saved, &null_fd);
  assert(saved == -1);
  assert(null_fd == -1);
  monitor_stderr_quiet_end(NULL, NULL);
}

int main(void)
{
  test_should_emit_always_when_not_first_only();
  test_should_emit_first_only_latches();
  test_should_emit_null_latched();
  test_failure_deltas();
  test_named_fail_note_and_clear();
  test_stderr_quiet_begin_end();
  printf("test_monitor_release_logging passed\n");
  return 0;
}
