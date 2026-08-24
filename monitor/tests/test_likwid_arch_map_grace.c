#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpuid.h"
#include "likwid_arch_map.h"

static void test_grace_cyc_ins_eventset(void)
{
  const char *es = likwid_arch_eventset_grace();

  assert(es != NULL);
  assert(strcmp(es, "CPU_CYCLES:PMC0,INST_RETIRED:PMC1") == 0);
  assert(strstr(es, "CPU_CYCLES:PMC0") != NULL);
  assert(strstr(es, "INST_RETIRED:PMC1") != NULL);
  /* Do not arm FLOPs / SVE INT system-wide (6 GPCs; PAPI starvation lesson). */
  assert(strstr(es, "FP_ARITH") == NULL);
  assert(strstr(es, "SVE") == NULL);
  assert(strstr(es, "ASE_SVE") == NULL);
}

static void test_grace_cyc_only_fallback(void)
{
  const char *es = likwid_arch_eventset_grace_cyc_only();

  assert(es != NULL);
  assert(strcmp(es, "CPU_CYCLES:PMC0") == 0);
  assert(strstr(es, "INST_RETIRED") == NULL);
  assert(strstr(es, ",") == NULL);
}

static void test_processor_arm_grace_uses_full_eventset(void)
{
  const char *es = likwid_arch_eventset_for_processor(ARM_GRACE, 6);

  assert(es != NULL);
  assert(strcmp(es, likwid_arch_eventset_grace()) == 0);
}

int main(void)
{
  test_grace_cyc_ins_eventset();
  test_grace_cyc_only_fallback();
  test_processor_arm_grace_uses_full_eventset();
  printf("test_likwid_arch_map_grace passed\n");
  return 0;
}
