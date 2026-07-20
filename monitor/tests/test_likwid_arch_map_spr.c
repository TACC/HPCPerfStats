#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpuid.h"
#include "likwid_arch_map.h"

static void test_spr_eventset_no_skx_uops(void)
{
  const char *es = likwid_arch_eventset_for_processor(SAPPHIRE_RAPIDS, 8);

  assert(es != NULL);
  assert(strstr(es, "INSTR_RETIRED_ANY:FIXC0") != NULL);
  assert(strstr(es, "CPU_CLK_UNHALTED_CORE:FIXC1") != NULL);
  assert(strstr(es, "CPU_CLK_UNHALTED_REF:FIXC2") != NULL);
  assert(strstr(es, "MEM_INST_RETIRED_ALL_LOADS") != NULL);
  assert(strstr(es, "L1D_REPLACEMENT") != NULL);
  assert(strstr(es, "MEM_LOAD_UOPS_RETIRED_") == NULL);
  assert(strstr(es, "FP_ARITH_INST_RETIRED_") == NULL);
}

static void test_icx_unchanged_shape(void)
{
  const char *es = likwid_arch_eventset_for_processor(ICELAKE_SERVER, 8);

  assert(es != NULL);
  assert(strstr(es, "MEM_INST_RETIRED_ALL_LOADS") != NULL);
  assert(strstr(es, "MEM_LOAD_UOPS_RETIRED_") == NULL);
}

int main(void)
{
  test_spr_eventset_no_skx_uops();
  test_icx_unchanged_shape();
  printf("test_likwid_arch_map_spr passed\n");
  return 0;
}
