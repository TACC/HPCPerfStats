#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "likwid_pmc_adapter.h"

static void test_sets_force_when_unset(void)
{
  unsetenv("LIKWID_FORCE");
  likwid_pmc_adapter_ensure_force_env();
  assert(getenv("LIKWID_FORCE") != NULL);
  assert(strcmp(getenv("LIKWID_FORCE"), "1") == 0);
}

static void test_respects_preset_zero(void)
{
  assert(setenv("LIKWID_FORCE", "0", 1) == 0);
  likwid_pmc_adapter_ensure_force_env();
  assert(strcmp(getenv("LIKWID_FORCE"), "0") == 0);
}

static void test_respects_preset_one(void)
{
  assert(setenv("LIKWID_FORCE", "1", 1) == 0);
  likwid_pmc_adapter_ensure_force_env();
  assert(strcmp(getenv("LIKWID_FORCE"), "1") == 0);
}

int main(void)
{
  test_sets_force_when_unset();
  test_respects_preset_zero();
  test_respects_preset_one();
  unsetenv("LIKWID_FORCE");
  printf("test_likwid_pmc_force_env passed\n");
  return 0;
}
