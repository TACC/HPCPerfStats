#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "likwid_uncore_profiles.h"

static void assert_has_colon(const char *events)
{
  assert(events != NULL);
  assert(strchr(events, ':') != NULL);
  assert(strchr(events, ' ') == NULL || strstr(events, ":") != NULL);
}

int main(void)
{
  const char *rome = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_DF_ROME);
  const char *milan = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_DF_MILAN);
  const char *genoa = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_DF_GENOA);
  const char *turin = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_DF_TURIN);

  assert_has_colon(rome);
  assert_has_colon(milan);
  assert_has_colon(genoa);
  assert_has_colon(turin);

  assert(strstr(rome, "DRAM_CHANNEL_0:DFC0") != NULL);
  assert(strstr(milan, "DRAM_CHANNEL_3:DFC3") != NULL);
  assert(strstr(genoa, "DRAM_READS_LOCAL_CHANNEL_0:DFC0") != NULL);
  assert(strstr(turin, "CAS_CMD_RD:UMC0C0") != NULL);

  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_DF_ROME, AMD_ROME));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_DF_ROME, AMD_TURIN));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_DF_TURIN, AMD_TURIN));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_DF_TURIN, AMD_MILAN));

  printf("test_amd_df_likwid_profiles passed\n");
  return 0;
}
