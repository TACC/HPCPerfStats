#include <assert.h>
#include <string.h>
#include "likwid_uncore_profiles.h"
#include "intel_processor.h"

static void test_profile_processor_match(void)
{
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX, CASCADE_LAKE));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_SKX, CASCADE_LAKE));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_ICX, ICELAKE_SERVER));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SPR, SAPPHIRE_RAPIDS));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX, SKYLAKE));
}

static void test_counter_map(void)
{
  char dev[32];
  const char *key = NULL;

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_SKX, "MBOX2C0", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "mbox2") == 0);
  assert(strcmp(key, "dram_cas_reads") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_SPR, "HBM3C1", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "hbm3") == 0);
  assert(strcmp(key, "hbm_cas_writes") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_ICX, "MDEV1C0", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "mdev1") == 0);
  assert(strcmp(key, "dram_cas_reads") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C0", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "cbox4") == 0);
  assert(strcmp(key, "llc_lookup_data_read_local") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C1", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "cbox4") == 0);
  assert(strcmp(key, "sf_evictions_mes") == 0);
}

static void test_eventset_nonempty(void)
{
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SKX) != NULL);
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_ICX) != NULL);
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX) != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SPR), "HBM0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX), "CBOX0C0") != NULL);
  assert(strstr(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_ONLY), "MBOX0C0") != NULL);
  assert(strstr(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_HBM_ONLY), "HBM0C0") != NULL);
  assert(strstr(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_ONLY), "HBM0C0") == NULL);
}

static void test_spr_try_order(void)
{
  likwid_spr_imc_eventset_t order[3];
  int n;
  int has_ddr;
  int has_hbm;

  /* Always DDR_HBM primary regardless of EDAC flags (Stampede3 HBM via LIKWID). */
  for (has_ddr = 0; has_ddr <= 1; has_ddr++) {
    for (has_hbm = 0; has_hbm <= 1; has_hbm++) {
      n = likwid_spr_imc_eventset_try_order(has_ddr, has_hbm, order, 3);
      assert(n == 3);
      assert(order[0] == LIKWID_SPR_IMC_EVT_DDR_HBM);
      assert(order[1] == LIKWID_SPR_IMC_EVT_DDR_ONLY);
      assert(order[2] == LIKWID_SPR_IMC_EVT_HBM_ONLY);
    }
  }

  assert(strcmp(likwid_spr_imc_eventset_variant_name(LIKWID_SPR_IMC_EVT_DDR_HBM), "DDR_HBM") == 0);
  assert(strcmp(likwid_spr_imc_eventset_variant_name(LIKWID_SPR_IMC_EVT_DDR_ONLY), "DDR_ONLY") ==
         0);
  assert(strcmp(likwid_spr_imc_eventset_variant_name(LIKWID_SPR_IMC_EVT_HBM_ONLY), "HBM_ONLY") == 0);

  assert(likwid_spr_imc_eventset_try_order(1, 1, NULL, 3) == 0);
  assert(likwid_spr_imc_eventset_try_order(1, 1, order, 0) == 0);
}

int main(void)
{
  test_profile_processor_match();
  test_counter_map();
  test_eventset_nonempty();
  test_spr_try_order();
  return 0;
}
