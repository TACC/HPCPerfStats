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
  const char *ddr_only;
  const char *ddr_hbm;
  const char *skx;
  const char *icx;
  const char *cha;

  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SKX) != NULL);
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_ICX) != NULL);
  assert(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX) != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SPR), "HBM0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX), "CBOX0C0") != NULL);
  assert(strstr(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_ONLY), "MBOX0C0") != NULL);
  assert(strstr(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_HBM_ONLY), "HBM0C0") != NULL);
  assert(strstr(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_ONLY), "HBM0C0") == NULL);

  /* LIKWID SPR table is MBOX0–11 only — must not request MBOX12+. */
  ddr_only = likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_ONLY);
  ddr_hbm = likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_HBM);
  assert(strstr(ddr_only, "MBOX11C0") != NULL);
  assert(strstr(ddr_only, "MBOX12C0") == NULL);
  assert(strstr(ddr_hbm, "MBOX11C0") != NULL);
  assert(strstr(ddr_hbm, "MBOX12C0") == NULL);
  assert(strstr(ddr_hbm, "HBM0C0") != NULL);
  assert(strstr(ddr_hbm, "HBM15C0") != NULL);

  /* LIKWID custom events require EVENT:COUNTER (colon), not COUNTER EVENT. */
  assert(strstr(ddr_only, "CAS_COUNT_RD:MBOX0C0") != NULL);
  assert(strstr(ddr_only, "CAS_COUNT_WR:MBOX0C1") != NULL);
  assert(strstr(ddr_hbm, "CAS_COUNT_RD:HBM0C0") != NULL);
  assert(strstr(ddr_only, "MBOX0C0 CAS_COUNT") == NULL);
  assert(strstr(ddr_hbm, "HBM0C0 CAS_COUNT") == NULL);

  skx = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SKX);
  icx = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_ICX);
  cha = likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX);
  assert(strstr(skx, "CAS_COUNT_RD:MBOX0C0") != NULL);
  assert(strstr(skx, "MBOX0C0 CAS_COUNT") == NULL);
  assert(strstr(icx, "DDR_READ_BYTES:MDEV0C0") != NULL);
  assert(strstr(icx, "MDEV0C0 DDR_READ") == NULL);
  assert(strstr(cha, "LLC_LOOKUP_DATA_READ:CBOX0C0") != NULL);
  assert(strstr(cha, "CBOX0C0 LLC_LOOKUP") == NULL);
}

static void assert_eventset_colon_tokens(const char *events)
{
  const char *p;
  const char *comma;

  assert(events != NULL);
  assert(strchr(events, ':') != NULL);
  p = events;
  while (*p != '\0') {
    comma = strchr(p, ',');
    if (comma == NULL)
      comma = p + strlen(p);
    /* Each token must contain ':' (EVENT:COUNTER). */
    assert(memchr(p, ':', (size_t)(comma - p)) != NULL);
    /* Reject legacy counter-first space form inside a token. */
    assert(memchr(p, ' ', (size_t)(comma - p)) == NULL);
    if (*comma == '\0')
      break;
    p = comma + 1;
  }
}

static void test_eventset_colon_format(void)
{
  int sizes[3];
  int n;
  int i;

  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SKX));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_ICX));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SPR));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX));
  assert_eventset_colon_tokens(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_ONLY));
  assert_eventset_colon_tokens(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_HBM_ONLY));
  assert_eventset_colon_tokens(likwid_spr_imc_eventset_string(LIKWID_SPR_IMC_EVT_DDR_HBM));

  n = likwid_spr_imc_hbm_ladder_sizes(sizes, 3);
  assert(n == 3);
  for (i = 0; i < n; i++)
    assert_eventset_colon_tokens(likwid_spr_imc_hbm_channels_eventset(sizes[i]));
  assert_eventset_colon_tokens(likwid_spr_imc_hbm_channels_eventset(16));
}

static void test_hbm_ladder(void)
{
  int sizes[4];
  int n;

  assert(strstr(likwid_spr_imc_hbm_channels_eventset(16), "HBM15C0") != NULL);
  assert(strstr(likwid_spr_imc_hbm_channels_eventset(8), "HBM7C0") != NULL);
  assert(strstr(likwid_spr_imc_hbm_channels_eventset(8), "HBM8C0") == NULL);
  assert(strstr(likwid_spr_imc_hbm_channels_eventset(4), "HBM3C0") != NULL);
  assert(strstr(likwid_spr_imc_hbm_channels_eventset(4), "HBM4C0") == NULL);
  assert(strstr(likwid_spr_imc_hbm_channels_eventset(1), "HBM0C0") != NULL);
  assert(strstr(likwid_spr_imc_hbm_channels_eventset(1), "HBM1C0") == NULL);
  assert(likwid_spr_imc_hbm_channels_eventset(0) == NULL);

  n = likwid_spr_imc_hbm_ladder_sizes(sizes, 4);
  assert(n == 3);
  assert(sizes[0] == 8);
  assert(sizes[1] == 4);
  assert(sizes[2] == 1);
  assert(likwid_spr_imc_hbm_ladder_sizes(NULL, 3) == 0);
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
  assert(strcmp(likwid_spr_imc_eventset_variant_name(LIKWID_SPR_IMC_EVT_HBM_ONLY), "HBM_ONLY") ==
         0);

  assert(likwid_spr_imc_eventset_try_order(1, 1, NULL, 3) == 0);
  assert(likwid_spr_imc_eventset_try_order(1, 1, order, 0) == 0);
}

int main(void)
{
  test_profile_processor_match();
  test_counter_map();
  test_eventset_nonempty();
  test_eventset_colon_format();
  test_hbm_ladder();
  test_spr_try_order();
  return 0;
}
