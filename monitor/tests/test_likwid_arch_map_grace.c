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

/* Grace DRAM CAS: SCF uncore events ride in the overlay's single group (no second group,
 * no per-tick re-arm). LIKWID 5.5.2 nvidia_grace MEM: CMEM_RD_DATA beats (32 B) and
 * CMEM_WR_TOTAL_BYTES (bytes) on SCF0/SCF1. */
static void test_grace_scf_eventset_extends_cyc_ins(void)
{
  const char *es = likwid_arch_eventset_grace_scf();
  const char *core = likwid_arch_eventset_grace();

  assert(es != NULL);
  assert(strncmp(es, core, strlen(core)) == 0);
  assert(strcmp(es + strlen(core), ",CMEM_RD_DATA:SCF0,CMEM_WR_TOTAL_BYTES:SCF1") == 0);
  assert(strstr(es, "GMEM") == NULL); /* GPU memory traffic is not DRAM */
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
  test_grace_scf_eventset_extends_cyc_ins();
  test_processor_arm_grace_uses_full_eventset();
  printf("test_likwid_arch_map_grace passed\n");
  return 0;
}
