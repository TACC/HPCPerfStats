#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "likwid_pmc_schema_map.h"

static void test_invalid_sentinel(void)
{
  assert(likwid_pmc_result_is_invalid(1ULL << 63));
  assert(!likwid_pmc_result_is_invalid(0ULL));
  assert(!likwid_pmc_result_is_invalid(418297637ULL));
}

static void test_fixc_index(void)
{
  assert(likwid_pmc_fixc_index("fixc0") == 0);
  assert(likwid_pmc_fixc_index("FIXC0") == 0);
  assert(likwid_pmc_fixc_index("Fixc1") == 1);
  assert(likwid_pmc_fixc_index("FIXC2") == 2);
  assert(likwid_pmc_fixc_index("PMC0") == -1);
  assert(likwid_pmc_fixc_index(NULL) == -1);
}

static void test_schema_key_map(void)
{
  char buf[128];
  const char *k;

  k = likwid_pmc_schema_key_from_event("INSTR_RETIRED_ANY", buf, sizeof(buf));
  assert(k != NULL && strcmp(k, "instr_retired_any") == 0);

  k = likwid_pmc_schema_key_from_event("CPU_CLK_UNHALTED_CORE", buf, sizeof(buf));
  assert(k != NULL && strcmp(k, "cycles_unhalted_core") == 0);

  k = likwid_pmc_schema_key_from_event("CPU_CLK_UNHALTED_REF", buf, sizeof(buf));
  assert(k != NULL && strcmp(k, "cycles_unhalted_ref") == 0);

  k = likwid_pmc_schema_key_from_event("L1D_REPLACEMENT", buf, sizeof(buf));
  assert(k != NULL && strcmp(k, "l1d_replacement") == 0);

  k = likwid_pmc_schema_key_from_event("MEM_INST_RETIRED_ALL_LOADS", buf, sizeof(buf));
  assert(k != NULL && strcmp(k, "mem_load_uops_retired_l1_hit") == 0);

  k = likwid_pmc_schema_key_from_event("FP_ARITH_INST_RETIRED_SCALAR_DOUBLE", buf, sizeof(buf));
  assert(k != NULL && strcmp(k, "fp_arith_inst_retired_scalar_double") == 0);

  assert(likwid_pmc_schema_key_from_event(NULL, buf, sizeof(buf)) == NULL);
  assert(likwid_pmc_schema_key_from_event("X", NULL, 8) == NULL);
}

int main(void)
{
  test_invalid_sentinel();
  test_fixc_index();
  test_schema_key_map();
  printf("test_likwid_pmc_schema_map passed\n");
  return 0;
}
