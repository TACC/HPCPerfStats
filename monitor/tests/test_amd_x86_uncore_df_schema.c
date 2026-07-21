#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "amd_x86_uncore_df.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char amd_df_schema_def[] = JOIN(AMD_X86_UNCORE_DF_KEYS);
#undef X

int main(void)
{
  assert(strstr(amd_df_schema_def, " dram_chan0_bytes,") != NULL);
  assert(strstr(amd_df_schema_def, " dram_chan3_bytes,") != NULL);
  assert(strstr(amd_df_schema_def, " EVENT_DRAM") == NULL);
  assert(strstr(amd_df_schema_def, "amd_x86_pmc") == NULL);
  printf("test_amd_x86_uncore_df_schema passed\n");
  return 0;
}
