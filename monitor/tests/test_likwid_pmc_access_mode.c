#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "likwid_pmc_access_mode.h"

static void test_unset_defaults_perf(void)
{
  int invalid = -1;

  unsetenv("HPCPERFSTATS_LIKWID_ACCESS");
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_PERF);
  assert(invalid == 0);
  assert(strcmp(likwid_pmc_access_mode_name(LIKWID_PMC_ACCESS_PERF), "perf") == 0);
}

static void test_empty_defaults_perf(void)
{
  int invalid = -1;

  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_PERF);
  assert(invalid == 0);
}

static void test_perf_explicit(void)
{
  int invalid = -1;

  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "perf", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_PERF);
  assert(invalid == 0);

  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "PERF", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_PERF);
  assert(invalid == 0);
}

static void test_direct_explicit(void)
{
  int invalid = -1;

  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "direct", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_DIRECT);
  assert(invalid == 0);
  assert(strcmp(likwid_pmc_access_mode_name(LIKWID_PMC_ACCESS_DIRECT), "direct") == 0);

  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "DIRECT", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_DIRECT);
  assert(invalid == 0);
}

static void test_invalid_falls_back_perf(void)
{
  int invalid = 0;

  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "msr", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_PERF);
  assert(invalid == 1);

  invalid = 0;
  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "accessdaemon", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(&invalid) == LIKWID_PMC_ACCESS_PERF);
  assert(invalid == 1);
}

static void test_null_invalid_out_param(void)
{
  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "bogus", 1) == 0);
  assert(likwid_pmc_access_mode_from_env(NULL) == LIKWID_PMC_ACCESS_PERF);
}

int main(void)
{
  test_unset_defaults_perf();
  test_empty_defaults_perf();
  test_perf_explicit();
  test_direct_explicit();
  test_invalid_falls_back_perf();
  test_null_invalid_out_param();
  unsetenv("HPCPERFSTATS_LIKWID_ACCESS");
  printf("test_likwid_pmc_access_mode passed\n");
  return 0;
}
