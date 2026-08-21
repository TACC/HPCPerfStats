#include <assert.h>
#include <string.h>
#include "JOIN.h"
#include "intel_spr_cha.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char intel_spr_cha_schema_def[] = JOIN(INTEL_SPR_CHA_KEYS);
#undef X
#undef INTEL_SPR_CHA_KEYS

int main(void)
{
  assert(strstr(intel_spr_cha_schema_def, " llc_lookup_data_read_local,") != NULL);
  assert(strstr(intel_spr_cha_schema_def, " sf_evictions_mes,") != NULL);
  assert(strstr(intel_spr_cha_schema_def, " bypass_cha_imc_all,") != NULL);
  assert(strstr(intel_spr_cha_schema_def, " llc_lookup_write,") == NULL);
  return 0;
}
