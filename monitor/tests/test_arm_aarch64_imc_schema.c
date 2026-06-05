#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "arm_imc.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char arm_aarch64_imc_schema_def[] = JOIN(ARM_AARCH64_IMC_KEYS);
#undef X

int main(void)
{
  assert(strcmp(ARM_AARCH64_IMC_ST_NAME, "arm_aarch64_imc") == 0);
  assert(strstr(arm_aarch64_imc_schema_def, " dram_cas_reads,") != NULL);
  assert(strstr(arm_aarch64_imc_schema_def, " dram_cas_writes,") != NULL);
  assert(strstr(arm_aarch64_imc_schema_def, " CAS_READS") == NULL);
  assert(strstr(arm_aarch64_imc_schema_def, " CAS_WRITES") == NULL);
  printf("test_arm_aarch64_imc_schema passed\n");
  return 0;
}
