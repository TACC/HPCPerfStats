/* roofline_hw_peak_detect_fill_cache with HPCPERFSTATS_SKIP_HW_PROBE fixture values. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpuid.h"
#include "roofline_hw_peak_detect.h"

int nr_cpus = 8;
int n_pmcs = 0;
processor_t processor = (processor_t)0;

static void test_skip_probe_uses_nr_cpus_fixture(void)
{
  struct roofline_cached_peaks cache;

  setenv("HPCPERFSTATS_SKIP_HW_PROBE", "1", 1);
  memset(&cache, 0, sizeof(cache));
  roofline_hw_peak_detect_fill_cache(&cache);

  assert(cache.initialized == 1);
  assert(cache.cpu_flops == 8000000000ULL);
  assert(cache.cpu_bw == 1000000000ULL);
  assert(cache.gpu_flops == 0ULL);
  assert(cache.gpu_mem_bw == 0ULL);
}

static void test_null_cache_is_noop(void)
{
  roofline_hw_peak_detect_fill_cache(NULL);
}

int main(void)
{
  test_skip_probe_uses_nr_cpus_fixture();
  test_null_cache_is_noop();
  printf("test_roofline_detect_fixture passed\n");
  return 0;
}
