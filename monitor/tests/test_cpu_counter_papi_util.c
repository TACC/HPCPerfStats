#include <assert.h>
#include <stdio.h>

#include "cpu_counter_metrics_papi_util.h"

static void test_desired_nofile(void)
{
  rlim_t soft = papi_desired_nofile_soft(72, 6);

  assert(soft >= 1024);
  assert(soft >= (rlim_t)(72 * (6 + 8) + 256));
  assert(papi_desired_nofile_soft(0, 6) == 1024);
}

static void test_shrink_active(void)
{
  /* First-N shrink: CYC must be placed first by caller so it is retained. */
  assert(papi_shrink_active_count(6, 4) == 4);
  assert(papi_shrink_active_count(3, 8) == 3);
  assert(papi_shrink_active_count(6, 0) == 6);
  assert(papi_shrink_active_count(0, 4) == 0);
  assert(papi_shrink_active_count(2, 1) == 1);
}

static void test_partial_attach(void)
{
  assert(papi_is_partial_attach(1, 72));
  assert(papi_is_partial_attach(71, 72));
  assert(!papi_is_partial_attach(72, 72));
  assert(!papi_is_partial_attach(0, 72));
  assert(!papi_is_partial_attach(5, 0));
}

static void test_filter_system_wide(void)
{
  int slots[6] = {PAPI_UTIL_SLOT_CYC, PAPI_UTIL_SLOT_SP,   PAPI_UTIL_SLOT_DP,
                  PAPI_UTIL_SLOT_INS, PAPI_UTIL_SLOT_INT8, PAPI_UTIL_SLOT_INT16};
  int codes[6] = {100, 101, 102, 103, 104, 105};
  int n;

  n = papi_filter_system_wide_events(slots, codes, 6, PAPI_SW_FILTER_CYC_INS);
  assert(n == 2);
  assert(slots[0] == PAPI_UTIL_SLOT_CYC);
  assert(codes[0] == 100);
  assert(slots[1] == PAPI_UTIL_SLOT_INS);
  assert(codes[1] == 103);

  slots[0] = PAPI_UTIL_SLOT_CYC;
  slots[1] = PAPI_UTIL_SLOT_INS;
  codes[0] = 100;
  codes[1] = 103;
  n = papi_filter_system_wide_events(slots, codes, 2, PAPI_SW_FILTER_CYC_ONLY);
  assert(n == 1);
  assert(slots[0] == PAPI_UTIL_SLOT_CYC);

  slots[0] = PAPI_UTIL_SLOT_SP;
  codes[0] = 101;
  assert(papi_filter_system_wide_events(slots, codes, 1, PAPI_SW_FILTER_CYC_INS) == 0);
  assert(papi_filter_system_wide_events(NULL, codes, 1, PAPI_SW_FILTER_CYC_INS) == 0);
}

static void test_census_threshold(void)
{
  assert(papi_census_min_nonzero_cyc(72) == 9); /* 72/8 */
  assert(papi_census_min_nonzero_cyc(16) == 4); /* floor 4 */
  assert(papi_census_min_nonzero_cyc(0) == 1);

  /* ok_cpus=72, only 3 nonzero → needs reshrink (Grace ops finding). */
  assert(papi_census_needs_reshrink(3, 72, 72));
  assert(papi_census_needs_reshrink(8, 72, 72)); /* min is 9 */
  assert(!papi_census_needs_reshrink(9, 72, 72));
  assert(!papi_census_needs_reshrink(40, 72, 72));
  /* Partial attach: do not reshrink based on census alone. */
  assert(!papi_census_needs_reshrink(0, 10, 72));
  assert(!papi_census_needs_reshrink(0, 0, 72));
}

int main(void)
{
  test_desired_nofile();
  test_shrink_active();
  test_partial_attach();
  test_filter_system_wide();
  test_census_threshold();
  printf("test_cpu_counter_papi_util passed\n");
  return 0;
}
