#include <assert.h>
#include <string.h>
#include "likwid_uncore_profiles.h"
#include "intel_processor.h"

static void test_profile_processor_match(void)
{
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX, SKYLAKE_X));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX, CASCADE_LAKE));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_SKX, SKYLAKE_X));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_SKX, CASCADE_LAKE));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_ICX, ICELAKE_SERVER));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_SPR, SAPPHIRE_RAPIDS));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_EMR, EMERALD_RAPIDS));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_GNR, GRANITE_RAPIDS));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_ICX, SKYLAKE_X));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_SPR, ICELAKE_SERVER));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_CHA_GNR, SIERRA_FOREST));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_ICX, ICELAKE_SERVER));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SPR, SAPPHIRE_RAPIDS));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_EMR, EMERALD_RAPIDS));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_GNR, GRANITE_RAPIDS));
  assert(likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SRF, SIERRA_FOREST));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SKX, SKYLAKE));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_SPR, EMERALD_RAPIDS));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_EMR, SAPPHIRE_RAPIDS));
  assert(!likwid_uncore_profile_matches_processor(LIKWID_UNCORE_PROFILE_IMC_GNR, SIERRA_FOREST));
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

  /* ICX CAS/MBOX counters keep historical mdevN device names. */
  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_ICX, "MBOX3C1", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "mdev3") == 0);
  assert(strcmp(key, "dram_cas_writes") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_IMC_ICX, "MBOX1C0", dev,
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

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C2:STATE=0x1F", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(key, "llc_lookup_write") == 0);
  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C3", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(key, "bypass_cha_imc_all") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_SPR, "CBOX2C2", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(key, "bypass_cha_imc_all") == 0);
  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_CHA_GNR, "CBOX1C0", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(key, "llc_lookup_data_read_local") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_DF_MILAN, "DFC2", dev, sizeof(dev),
                                           &key) == 0);
  assert(strcmp(dev, "df") == 0);
  assert(strcmp(key, "dram_chan2_bytes") == 0);

  assert(likwid_uncore_profile_map_counter(LIKWID_UNCORE_PROFILE_DF_TURIN, "UMC1C0", dev,
                                           sizeof(dev), &key) == 0);
  assert(strcmp(dev, "df") == 0);
  assert(strcmp(key, "dram_chan1_bytes") == 0);
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
  assert(strstr(icx, "CAS_COUNT_RD:MBOX0C0") != NULL);
  assert(strstr(icx, "CAS_COUNT_RD:MBOX11C0") != NULL);
  assert(strstr(icx, "DDR_READ_BYTES:MDEV") == NULL);
  assert(strstr(icx, "MBOX12C0") == NULL);
  assert(strstr(cha, "LLC_LOOKUP_DATA_READ:CBOX0C0:STATE=0x1F") != NULL);
  assert(strstr(cha, "CBOX0C0 LLC_LOOKUP") == NULL);
  assert(strstr(cha, "LLC_LOOKUP_WRITE:CBOX0C2:STATE=0x1F") != NULL);
  assert(strstr(cha, "BYPASS_CHA_IMC_TAKEN:CBOX0C3") != NULL);

  /* Gen-specific CHA names (do not reuse SKX strings on SPR/GNR). */
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SPR),
                "LLC_LOOKUP_DATA_RD:CBOX0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SPR),
                "LLC_LOOKUP_DATA_READ:") == NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_GNR),
                "REQUESTS_READS:CBOX0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_GNR), "LLC_LOOKUP") ==
         NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_ICX),
                "LLC_LOOKUP_WRITES_AND_OTHER:CBOX0C2") != NULL);

  /* EMR reuses SPR CAS_COUNT_RD; GNR/SRF require SCH0 names. */
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_EMR),
                "CAS_COUNT_RD:MBOX0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_EMR),
                "CAS_COUNT_SCH0_RD") == NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_GNR),
                "CAS_COUNT_SCH0_RD:MBOX0C0") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_GNR),
                "CAS_COUNT_RD:MBOX") == NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SRF),
                "CAS_COUNT_SCH0_WR:MBOX11C1") != NULL);
  assert(strstr(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_GNR), "MBOX12C0") == NULL);
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
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_EMR));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_GNR));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_IMC_SRF));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_ICX));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_SPR));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_EMR));
  assert_eventset_colon_tokens(likwid_uncore_profile_eventset(LIKWID_UNCORE_PROFILE_CHA_GNR));
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

static void test_icx_try_order(void)
{
  likwid_icx_imc_eventset_t order[3];
  int n;
  const char *es;

  n = likwid_icx_imc_eventset_try_order(order, 3);
  assert(n == 3);
  assert(order[0] == LIKWID_ICX_IMC_EVT_MBOX12);
  assert(order[1] == LIKWID_ICX_IMC_EVT_MBOX6);
  assert(order[2] == LIKWID_ICX_IMC_EVT_MBOX4);

  es = likwid_icx_imc_eventset_string(LIKWID_ICX_IMC_EVT_MBOX12);
  assert(strstr(es, "CAS_COUNT_RD:MBOX0C0") != NULL);
  assert(strstr(es, "CAS_COUNT_RD:MBOX11C0") != NULL);
  assert(strstr(likwid_icx_imc_eventset_string(LIKWID_ICX_IMC_EVT_MBOX6), "MBOX5C0") != NULL);
  assert(strstr(likwid_icx_imc_eventset_string(LIKWID_ICX_IMC_EVT_MBOX6), "MBOX6C0") == NULL);
  assert(strstr(likwid_icx_imc_eventset_string(LIKWID_ICX_IMC_EVT_MBOX4), "MBOX3C0") != NULL);
  assert(strstr(likwid_icx_imc_eventset_string(LIKWID_ICX_IMC_EVT_MBOX4), "MBOX4C0") == NULL);
  assert(strcmp(likwid_icx_imc_eventset_variant_name(LIKWID_ICX_IMC_EVT_MBOX12), "MBOX12") == 0);
  assert(likwid_icx_imc_eventset_try_order(NULL, 3) == 0);
  assert(likwid_icx_imc_eventset_try_order(order, 0) == 0);
}

static void test_cha_ladder_and_build(void)
{
  int sizes[8];
  int n;
  char buf[4096];

  assert(likwid_cha_profile_is_cha(LIKWID_UNCORE_PROFILE_CHA_SKX));
  assert(!likwid_cha_profile_is_cha(LIKWID_UNCORE_PROFILE_IMC_SKX));
  assert(likwid_cha_profile_cbox_max(LIKWID_UNCORE_PROFILE_CHA_SKX) == 28);
  assert(likwid_cha_profile_cbox_max(LIKWID_UNCORE_PROFILE_CHA_ICX) == 40);
  assert(likwid_cha_profile_cbox_max(LIKWID_UNCORE_PROFILE_CHA_SPR) == 60);
  assert(likwid_cha_events_per_cbox(LIKWID_UNCORE_PROFILE_CHA_SKX) == 4);
  assert(likwid_cha_events_per_cbox(LIKWID_UNCORE_PROFILE_CHA_SPR) == 3);
  assert(likwid_cha_events_per_cbox(LIKWID_UNCORE_PROFILE_CHA_GNR) == 2);

  /* Live SKX 24 CHA → 24 then 16 then 8. */
  n = likwid_cha_ladder_sizes(24, 28, sizes, 8);
  assert(n == 3);
  assert(sizes[0] == 24);
  assert(sizes[1] == 16);
  assert(sizes[2] == 8);

  /* SPR 60 → 60, 28, 16, 8. */
  n = likwid_cha_ladder_sizes(60, 60, sizes, 8);
  assert(n == 4);
  assert(sizes[0] == 60);
  assert(sizes[1] == 28);
  assert(sizes[2] == 16);
  assert(sizes[3] == 8);

  /* ICX 40 → 40, 28, 16, 8. */
  n = likwid_cha_ladder_sizes(40, 40, sizes, 8);
  assert(n == 4);
  assert(sizes[0] == 40);

  n = likwid_cha_ladder_sizes(0, 28, sizes, 8);
  assert(n >= 1);
  assert(sizes[0] == 8);

  assert(likwid_cha_build_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX, 2, buf, sizeof(buf)) == 0);
  assert(strstr(buf, "LLC_LOOKUP_DATA_READ:CBOX0C0:STATE=0x1F") != NULL);
  assert(strstr(buf, "LLC_LOOKUP_DATA_READ:CBOX1C0:STATE=0x1F") != NULL);
  assert(strstr(buf, "BYPASS_CHA_IMC_TAKEN:CBOX1C3") != NULL);
  assert(strstr(buf, "CBOX2C0") == NULL);

  assert(likwid_cha_build_eventset(LIKWID_UNCORE_PROFILE_CHA_SPR, 1, buf, sizeof(buf)) == 0);
  assert(strstr(buf, "LLC_LOOKUP_DATA_RD:CBOX0C0") != NULL);
  assert(strstr(buf, "DATA_READ") == NULL);
  assert(strstr(buf, "BYPASS_CHA_IMC_TAKEN:CBOX0C2") != NULL);

  assert(likwid_cha_build_eventset(LIKWID_UNCORE_PROFILE_CHA_GNR, 1, buf, sizeof(buf)) == 0);
  assert(strstr(buf, "REQUESTS_READS:CBOX0C0") != NULL);
  assert(strstr(buf, "LLC_VICTIMS_LOCAL_M:CBOX0C1") != NULL);
  assert(strstr(buf, "LLC_LOOKUP") == NULL);

  assert(likwid_cha_build_eventset(LIKWID_UNCORE_PROFILE_CHA_SKX, 1, NULL, 0) < 0);
  assert(likwid_cha_ladder_sizes(24, 28, NULL, 8) == 0);
}

int main(void)
{
  test_profile_processor_match();
  test_counter_map();
  test_eventset_nonempty();
  test_eventset_colon_format();
  test_hbm_ladder();
  test_spr_try_order();
  test_icx_try_order();
  test_cha_ladder_and_build();
  return 0;
}
