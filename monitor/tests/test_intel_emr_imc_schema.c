#include <assert.h>
#include <string.h>
#include "JOIN.h"
#include "intel_emr_imc.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char intel_emr_imc_schema_def[] = JOIN(INTEL_EMR_IMC_KEYS);
#undef X
#undef INTEL_EMR_IMC_KEYS

int main(void)
{
  assert(strstr(intel_emr_imc_schema_def, " dram_cas_reads,") != NULL);
  assert(strstr(intel_emr_imc_schema_def, " hbm_cas_reads,") != NULL);
  assert(strstr(intel_emr_imc_schema_def, " hbm_cas_writes,") != NULL);
  return 0;
}
