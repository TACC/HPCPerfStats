#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "cpuid.h"
#include "likwid_rapl.h"

processor_t processor;
int nr_cpus = 1;

int
main(void)
{
  /* Scale: 1 J per LSB -> 1000 mJ per increment */
  assert(likwid_rapl_raw_to_mj(1u, 1.0) == 1000ULL);
  assert(likwid_rapl_raw_to_mj(2u, 1.0) == 2000ULL);

  /*
   * LIKWID power_read writes uint64_t; RAPL energy status is the low 32 bits.
   * Truncation must ignore high bits (regression for stack smash from uint32_t*).
   */
  assert(likwid_rapl_energy_status_lo32(0x1ULL) == 1u);
  assert(likwid_rapl_energy_status_lo32(0x100000001ULL) == 1u);
  assert(likwid_rapl_energy_status_lo32(0xffffffff00000002ULL) == 2u);
  assert(likwid_rapl_raw_to_mj(likwid_rapl_energy_status_lo32(0xabc00000002ULL),
                               1.0) == 2000ULL);

  printf("test_likwid_rapl_scale passed\n");
  return 0;
}
