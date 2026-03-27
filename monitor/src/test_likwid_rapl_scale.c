#include <assert.h>
#include <stdint.h>
#include <stdio.h>

static unsigned long long
raw_to_mj(uint32_t raw, double joules_per_lsb)
{
  return (unsigned long long)((double)raw * joules_per_lsb * 1000.0 + 0.5);
}

int
main(void)
{
  /* 1 J per LSB -> 1000 mJ per increment */
  assert(raw_to_mj(1u, 1.0) == 1000ULL);
  assert(raw_to_mj(2u, 1.0) == 2000ULL);
  printf("likwid_rapl scale test passed\n");
  return 0;
}
