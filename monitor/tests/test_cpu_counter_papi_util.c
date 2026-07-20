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
  assert(papi_shrink_active_count(6, 4) == 4);
  assert(papi_shrink_active_count(3, 8) == 3);
  assert(papi_shrink_active_count(6, 0) == 6);
  assert(papi_shrink_active_count(0, 4) == 0);
}

static void test_partial_attach(void)
{
  assert(papi_is_partial_attach(1, 72));
  assert(papi_is_partial_attach(71, 72));
  assert(!papi_is_partial_attach(72, 72));
  assert(!papi_is_partial_attach(0, 72));
  assert(!papi_is_partial_attach(5, 0));
}

int main(void)
{
  test_desired_nofile();
  test_shrink_active();
  test_partial_attach();
  printf("test_cpu_counter_papi_util passed\n");
  return 0;
}
