#include <assert.h>
#include <string.h>
#include "JOIN.h"
#include "intel_snb_imc.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char intel_snb_imc_schema_def[] = JOIN(INTEL_SNB_IMC_KEYS);
#undef X
#undef INTEL_SNB_IMC_KEYS

int main(void)
{
  assert(strstr(intel_snb_imc_schema_def, " dram_cas_reads,") != NULL);
  assert(strstr(intel_snb_imc_schema_def, " dram_fixed_ctr,") != NULL);
  return 0;
}
