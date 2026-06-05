#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "cpuid.h"

processor_t processor;
int nr_cpus = 1;

#ifdef HAVE_LIKWID
#include "likwid_rapl.h"
#endif

static unsigned long long
raw_to_mj(uint32_t raw, double joules_per_lsb)
{
#ifdef HAVE_LIKWID
  return likwid_rapl_raw_to_mj(raw, joules_per_lsb);
#else
  return (unsigned long long)((double)raw * joules_per_lsb * 1000.0 + 0.5);
#endif
}

int
main(void)
{
  /* 1 J per LSB -> 1000 mJ per increment */
  assert(raw_to_mj(1u, 1.0) == 1000ULL);
  assert(raw_to_mj(2u, 1.0) == 2000ULL);
  printf("test_likwid_rapl_scale passed\n");
  return 0;
}
