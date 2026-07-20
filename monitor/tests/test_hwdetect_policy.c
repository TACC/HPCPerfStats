#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "hwdetect.h"
#include "stats.h"

struct stats_type *stats_type_get(const char *name)
{
  (void)name;
  return NULL;
}

void ib_family_disable_all(void) {}

static void test_default_disable_on_first_miss(void)
{
  unsetenv("HPCPERFSTATS_NVIDIA_DISABLE_MISS_THRESHOLD");
  hwdetect_reset_nvidia_disable_state();
  assert(hwdetect_should_disable_nvidia_gpu(0) == 1);
}

static void test_threshold_debounces_transient_miss(void)
{
  setenv("HPCPERFSTATS_NVIDIA_DISABLE_MISS_THRESHOLD", "3", 1);
  hwdetect_reset_nvidia_disable_state();
  assert(hwdetect_should_disable_nvidia_gpu(0) == 0);
  assert(hwdetect_should_disable_nvidia_gpu(0) == 0);
  assert(hwdetect_should_disable_nvidia_gpu(1) == 0);
  assert(hwdetect_should_disable_nvidia_gpu(0) == 0);
  assert(hwdetect_should_disable_nvidia_gpu(0) == 0);
  assert(hwdetect_should_disable_nvidia_gpu(0) == 1);
}

int main(void)
{
  test_default_disable_on_first_miss();
  test_threshold_debounces_transient_miss();
  printf("test_hwdetect_policy passed\n");
  return 0;
}
