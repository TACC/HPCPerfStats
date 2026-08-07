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

static void test_emr_gnr_reuse_icx_shape(void)
{
  const char *emr = likwid_arch_eventset_for_processor(EMERALD_RAPIDS, 8);
  const char *gnr = likwid_arch_eventset_for_processor(GRANITE_RAPIDS, 8);

  assert(emr != NULL);
  assert(gnr != NULL);
  assert(strstr(emr, "MEM_INST_RETIRED_ALL_LOADS") != NULL);
  assert(strstr(emr, "L1D_REPLACEMENT") != NULL);
  assert(strstr(emr, "MEM_LOAD_UOPS_RETIRED_") == NULL);
  assert(strstr(gnr, "MEM_INST_RETIRED_ALL_LOADS") != NULL);
  assert(strstr(gnr, "L1D_REPLACEMENT") != NULL);
  assert(strstr(gnr, "MEM_LOAD_UOPS_RETIRED_") == NULL);
}

static void test_srf_atom_like_no_l1d_replacement(void)
{
  const char *es = likwid_arch_eventset_for_processor(SIERRA_FOREST, 8);

  assert(es != NULL);
  assert(strstr(es, "INSTR_RETIRED_ANY:FIXC0") != NULL);
  assert(strstr(es, "MEM_LOAD_UOPS_RETIRED_L1_HIT") != NULL);
  assert(strstr(es, "MEM_LOAD_UOPS_RETIRED_L2_HIT") != NULL);
  assert(strstr(es, "MEM_LOAD_UOPS_RETIRED_L3_HIT") != NULL);
  assert(strstr(es, "L1D_REPLACEMENT") == NULL);
  assert(strstr(es, "MEM_INST_RETIRED_ALL_LOADS") == NULL);
}

static void test_skx_clx_share_skx_era_eventset(void)
{
  const char *skx = likwid_arch_eventset_for_processor(SKYLAKE_X, 8);
  const char *clx = likwid_arch_eventset_for_processor(CASCADE_LAKE, 8);

  assert(skx != NULL);
  assert(clx != NULL);
  assert(strcmp(skx, clx) == 0);
  assert(strstr(skx, "MEM_LOAD_UOPS_RETIRED_L1_HIT") != NULL);
}

static void test_amd_turin_uses_ls_dispatch_all(void)
{
  const char *es = likwid_arch_eventset_for_processor(AMD_TURIN, 6);
  const char *p;

  assert(es != NULL);
  assert(strstr(es, "RETIRED_INSTRUCTIONS:PMC0") != NULL);
  assert(strstr(es, "RETIRED_BRANCH_INSTR:PMC1") != NULL);
  assert(strstr(es, "RETIRED_MISP_BRANCH_INSTR:PMC2") != NULL);
  assert(strstr(es, "LS_DISPATCH_ALL:PMC3") != NULL);
  /* Bare LS_DISPATCH has no default umask in LIKWID Zen tables. */
  for (p = es; (p = strstr(p, "LS_DISPATCH")) != NULL; p++) {
    assert(strncmp(p, "LS_DISPATCH_ALL", 15) == 0);
  }
}

static void test_amd_genoa_same_core_eventset(void)
{
  const char *es = likwid_arch_eventset_for_processor(AMD_GENOA, 6);
  const char *p;

  assert(es != NULL);
  assert(strstr(es, "LS_DISPATCH_ALL:PMC3") != NULL);
  for (p = es; (p = strstr(p, "LS_DISPATCH")) != NULL; p++) {
    assert(strncmp(p, "LS_DISPATCH_ALL", 15) == 0);
  }
}

int main(void)
{
  test_spr_eventset_no_skx_uops();
  test_icx_unchanged_shape();
  test_emr_gnr_reuse_icx_shape();
  test_srf_atom_like_no_l1d_replacement();
  test_skx_clx_share_skx_era_eventset();
  test_amd_turin_uses_ls_dispatch_all();
  test_amd_genoa_same_core_eventset();
  printf("test_likwid_arch_map_spr passed\n");
  return 0;
}
