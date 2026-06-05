/* MAD backoff: fresh state allows collection; fail streak blocks at threshold. */
#include <assert.h>
#include <stdio.h>

#include "ib_mad.h"

static void test_fresh_state_allows_collect(void)
{
  ib_mad_test_reset_backoff();
  assert(ib_mad_ext_collect_cycle_ok() == 1);
  assert(ib_mad_sw_collect_cycle_ok() == 1);
}

static void test_fail_streak_blocks_at_threshold(void)
{
  ib_mad_test_reset_backoff();
  ib_mad_test_set_ext_fail_streak(8);
  assert(ib_mad_ext_collect_cycle_ok() == 0);

  ib_mad_test_reset_backoff();
  ib_mad_test_set_sw_fail_streak(8);
  assert(ib_mad_sw_collect_cycle_ok() == 0);

  ib_mad_test_reset_backoff();
  ib_mad_test_set_ext_fail_streak(7);
  assert(ib_mad_ext_collect_cycle_ok() == 1);
}

int main(void)
{
  test_fresh_state_allows_collect();
  test_fail_streak_blocks_at_threshold();
  printf("test_ib_mad_backoff passed\n");
  return 0;
}
