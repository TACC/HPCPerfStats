/* OPA MAD backoff: fresh allows collect; fail streak blocks at threshold. */
#include <assert.h>
#include <stdio.h>

#include "opa_mad_backoff.h"

static void test_fresh_allows(void)
{
  opa_mad_test_reset_backoff();
  assert(opa_mad_collect_cycle_ok() == 1);
}

static void test_fail_streak_blocks(void)
{
  opa_mad_test_reset_backoff();
  opa_mad_test_set_fail_streak(8);
  assert(opa_mad_collect_cycle_ok() == 0);

  opa_mad_test_reset_backoff();
  opa_mad_test_set_fail_streak(7);
  assert(opa_mad_collect_cycle_ok() == 1);
}

static void test_success_clears(void)
{
  opa_mad_test_reset_backoff();
  opa_mad_note_failure();
  opa_mad_note_failure();
  opa_mad_note_success();
  assert(opa_mad_collect_cycle_ok() == 1);
}

int main(void)
{
  test_fresh_allows();
  test_fail_streak_blocks();
  test_success_clears();
  printf("test_opa_mad_backoff passed\n");
  return 0;
}
