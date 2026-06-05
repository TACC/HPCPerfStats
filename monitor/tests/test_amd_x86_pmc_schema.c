#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "amd64_pmc.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char amd_x86_pmc_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#define X SCHEMA_DEF
static const char amd_x86_df_schema_def[] = JOIN(DF_KEYS);
#undef X
#undef DF_KEYS

int main(void)
{
  assert(strstr(amd_x86_pmc_schema_def, " fp_ops_retired,") != NULL);
  assert(strstr(amd_x86_pmc_schema_def, " instr_retired,") != NULL);
  assert(strstr(amd_x86_pmc_schema_def, " aperf,") != NULL);
  assert(strstr(amd_x86_pmc_schema_def, " FLOPS") == NULL);
  assert(strstr(amd_x86_df_schema_def, " dram_chan0_bytes,") != NULL);
  assert(strstr(amd_x86_df_schema_def, " EVENT_DRAM") == NULL);
  printf("test_amd_x86_pmc_schema passed\n");
  return 0;
}
