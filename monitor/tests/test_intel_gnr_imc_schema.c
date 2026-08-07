#include <assert.h>
#include <string.h>
#include "JOIN.h"
#include "intel_gnr_imc.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char intel_gnr_imc_schema_def[] = JOIN(INTEL_GNR_IMC_KEYS);
#undef X
#undef INTEL_GNR_IMC_KEYS

int main(void)
{
  assert(strstr(intel_gnr_imc_schema_def, " dram_cas_reads,") != NULL);
  assert(strstr(intel_gnr_imc_schema_def, " dram_cas_writes,") != NULL);
  assert(strstr(intel_gnr_imc_schema_def, "hbm_cas") == NULL);
  return 0;
}
