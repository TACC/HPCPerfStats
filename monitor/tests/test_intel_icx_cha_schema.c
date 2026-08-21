#include <assert.h>
#include <string.h>
#include "JOIN.h"
#include "intel_icx_cha.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char intel_icx_cha_schema_def[] = JOIN(INTEL_ICX_CHA_KEYS);
#undef X
#undef INTEL_ICX_CHA_KEYS

int main(void)
{
  assert(strstr(intel_icx_cha_schema_def, " llc_lookup_data_read_local,") != NULL);
  assert(strstr(intel_icx_cha_schema_def, " llc_lookup_write,") != NULL);
  assert(strstr(intel_icx_cha_schema_def, " bypass_cha_imc_all,") != NULL);
  return 0;
}
