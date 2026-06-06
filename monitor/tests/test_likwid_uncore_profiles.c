#include <assert.h>
#include <string.h>
#include "likwid_uncore_profiles.h"
#include "intel_processor.h"

static void test_profile_processor_match(void)
{
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX,
                                                 CASCADE_LAKE));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_SKX,
                                                 CASCADE_LAKE));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_ICX,
                                                 ICELAKE_SERVER));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SPR,
                                                 SAPPHIRE_RAPIDS));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX,
                                                  SKYLAKE));
}

static void test_counter_map(void)
{
  char dev[32];
  const char *key = NULL;

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_SKX,
                                           "MBOX2C0", dev, sizeof(dev),
                                           &key) == 0);
  assert(strcmp(dev, "mbox2") == 0);
  assert(strcmp(key, "dram_cas_reads") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_SPR,
                                           "HBM3C1", dev, sizeof(dev),
                                           &key) == 0);
  assert(strcmp(dev, "hbm3") == 0);
  assert(strcmp(key, "hbm_cas_writes") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_ICX,
                                           "MDEV1C0", dev, sizeof(dev),
                                           &key) == 0);
  assert(strcmp(dev, "mdev1") == 0);
  assert(strcmp(key, "dram_cas_reads") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SKX,
                                           "CBOX4C0", dev, sizeof(dev),
                                           &key) == 0);
  assert(strcmp(dev, "cbox4") == 0);
  assert(strcmp(key, "llc_lookup_data_read_local") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SKX,
                                           "CBOX4C1", dev, sizeof(dev),
                                           &key) == 0);
  assert(strcmp(dev, "cbox4") == 0);
  assert(strcmp(key, "sf_evictions_mes") == 0);
}

static void test_eventset_nonempty(void)
{
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SKX) != NULL);
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_ICX) != NULL);
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX) != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SPR),
                "HBM0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX),
                "CBOX0C0") != NULL);
}

int main(void)
{
  test_profile_processor_match();
  test_counter_map();
  test_eventset_nonempty();
  return 0;
}
