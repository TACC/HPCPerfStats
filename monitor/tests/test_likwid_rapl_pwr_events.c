#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "likwid_rapl_pwr.h"

static void test_intel_eventset_has_pwr_pkg(void)
{
  const char *es = likwid_rapl_pwr_intel_eventset();

  assert(es != NULL);
  assert(strstr(es, "PWR_PKG_ENERGY:PWR0") != NULL);
  assert(strstr(es, "PWR_DRAM_ENERGY:PWR3") != NULL);
}

static void test_amd_eventset_pkg_only(void)
{
  const char *es = likwid_rapl_pwr_amd_eventset();

  assert(es != NULL);
  assert(strcmp(es, "PWR_PKG_ENERGY:PWR0") == 0);
}

static void test_schema_key_map_intel(void)
{
  assert(strcmp(likwid_rapl_pwr_schema_key_from_event("PWR_PKG_ENERGY", 0), "pkg_energy") == 0);
  assert(strcmp(likwid_rapl_pwr_schema_key_from_event("PWR_PP0_ENERGY", 0), "pp0_energy") == 0);
  assert(strcmp(likwid_rapl_pwr_schema_key_from_event("PWR_PP1_ENERGY", 0), "pp1_energy") == 0);
  assert(strcmp(likwid_rapl_pwr_schema_key_from_event("PWR_DRAM_ENERGY", 0), "dram_energy") == 0);
  assert(likwid_rapl_pwr_schema_key_from_event("INSTR_RETIRED_ANY", 0) == NULL);
  assert(likwid_rapl_pwr_schema_key_from_event(NULL, 0) == NULL);
}

static void test_schema_key_map_amd(void)
{
  assert(strcmp(likwid_rapl_pwr_schema_key_from_event("PWR_PKG_ENERGY", 1), "pkg_energy") == 0);
  assert(strcmp(likwid_rapl_pwr_schema_key_from_event("PWR_PP0_ENERGY", 1), "core_energy") == 0);
}

static void test_joules_to_mj(void)
{
  assert(likwid_rapl_joules_to_mj(1.0) == 1000ULL);
  assert(likwid_rapl_joules_to_mj(0.5) == 500ULL);
  assert(likwid_rapl_joules_to_mj(-1.0) == 0ULL);
  assert(likwid_rapl_joules_to_mj(0.0) == 0ULL);
}

static void test_result_usable_rejects_flat_zero(void)
{
  assert(likwid_rapl_pwr_result_usable(1.0) == 1);
  assert(likwid_rapl_pwr_result_usable(0.0) == 0);
  assert(likwid_rapl_pwr_result_usable(-0.1) == 0);
}

static void test_intel_eventset_for_domains(void)
{
  const char *es;

  es = likwid_rapl_pwr_intel_eventset_for_domains(1, 1, 0, 0);
  assert(es != NULL);
  assert(strstr(es, "PWR_PKG_ENERGY:PWR0") != NULL);
  assert(strstr(es, "PWR_DRAM_ENERGY:PWR3") != NULL);
  assert(strstr(es, "PWR_PP0_ENERGY") == NULL);
  assert(strstr(es, "PWR_PP1_ENERGY") == NULL);

  es = likwid_rapl_pwr_intel_eventset_for_domains(1, 1, 1, 0);
  assert(es != NULL);
  assert(strstr(es, "PWR_PP0_ENERGY:PWR1") != NULL);
  assert(strstr(es, "PWR_PP1_ENERGY") == NULL);

  es = likwid_rapl_pwr_intel_eventset_for_domains(1, 1, 1, 1);
  assert(es != NULL);
  assert(strstr(es, "PWR_PP1_ENERGY:PWR2") != NULL);

  es = likwid_rapl_pwr_intel_eventset_for_domains(1, 0, 0, 0);
  assert(es != NULL);
  assert(strcmp(es, "PWR_PKG_ENERGY:PWR0") == 0);

  assert(likwid_rapl_pwr_intel_eventset_for_domains(0, 0, 0, 0) == NULL);
}

int main(void)
{
  test_intel_eventset_has_pwr_pkg();
  test_intel_eventset_for_domains();
  test_amd_eventset_pkg_only();
  test_schema_key_map_intel();
  test_schema_key_map_amd();
  test_joules_to_mj();
  test_result_usable_rejects_flat_zero();
  printf("test_likwid_rapl_pwr_events passed\n");
  return 0;
}
