#include <assert.h>
#include <string.h>
#include "JOIN.h"
#include "intel_skx_cha.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char intel_skx_cha_schema_def[] = JOIN(INTEL_SKX_CHA_KEYS);
#undef X
#undef INTEL_SKX_CHA_KEYS

int main(void)
{
  assert(strstr(intel_skx_cha_schema_def, " sf_evictions_mes,") != NULL);
  assert(strstr(intel_skx_cha_schema_def, " llc_lookup_data_read_local,") != NULL);
  assert(strstr(intel_skx_cha_schema_def, " bypass_cha_imc_all,") != NULL);
  assert(strstr(intel_skx_cha_schema_def, " llc_lookup_write,") != NULL);
  return 0;
}
