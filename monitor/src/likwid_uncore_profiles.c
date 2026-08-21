#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "likwid_uncore_profiles.h"
#include "intel_processor.h"
#include "amd_processor.h"

/*
 * LIKWID perfmon_addEventSet: strings without ':' are treated as named
 * performance groups (perfgroup_readGroup). Custom events must use
 * EVENT:COUNTER (colon), matching core PMC form in likwid_arch_map.c.
 */

#define MBOX4_IMC_EVENTS                                                                           \
  "CAS_COUNT_RD:MBOX0C0,CAS_COUNT_WR:MBOX0C1,"                                                     \
  "CAS_COUNT_RD:MBOX1C0,CAS_COUNT_WR:MBOX1C1,"                                                     \
  "CAS_COUNT_RD:MBOX2C0,CAS_COUNT_WR:MBOX2C1,"                                                     \
  "CAS_COUNT_RD:MBOX3C0,CAS_COUNT_WR:MBOX3C1"

#define MBOX6_IMC_EVENTS                                                                           \
  MBOX4_IMC_EVENTS ","                                                                             \
                   "CAS_COUNT_RD:MBOX4C0,CAS_COUNT_WR:MBOX4C1,"                                    \
                   "CAS_COUNT_RD:MBOX5C0,CAS_COUNT_WR:MBOX5C1"

/* LIKWID 5.5.2 SPR counters.h defines MBOX0–11 only (not MBOX12–15). */
#define MBOX12_IMC_EVENTS                                                                          \
  MBOX6_IMC_EVENTS ","                                                                             \
                   "CAS_COUNT_RD:MBOX6C0,CAS_COUNT_WR:MBOX6C1,"                                    \
                   "CAS_COUNT_RD:MBOX7C0,CAS_COUNT_WR:MBOX7C1,"                                    \
                   "CAS_COUNT_RD:MBOX8C0,CAS_COUNT_WR:MBOX8C1,"                                    \
                   "CAS_COUNT_RD:MBOX9C0,CAS_COUNT_WR:MBOX9C1,"                                    \
                   "CAS_COUNT_RD:MBOX10C0,CAS_COUNT_WR:MBOX10C1,"                                  \
                   "CAS_COUNT_RD:MBOX11C0,CAS_COUNT_WR:MBOX11C1"

/* Legacy DDR_*:MDEV* removed — Stampede3 ICX PERF uses CAS_COUNT MBOX* (see MBOX12). */

#define HBM1_EVENTS "CAS_COUNT_RD:HBM0C0,CAS_COUNT_WR:HBM0C1"

#define HBM4_EVENTS                                                                                \
  HBM1_EVENTS ","                                                                                  \
              "CAS_COUNT_RD:HBM1C0,CAS_COUNT_WR:HBM1C1,"                                           \
              "CAS_COUNT_RD:HBM2C0,CAS_COUNT_WR:HBM2C1,"                                           \
              "CAS_COUNT_RD:HBM3C0,CAS_COUNT_WR:HBM3C1"

#define HBM8_EVENTS                                                                                \
  HBM4_EVENTS ","                                                                                  \
              "CAS_COUNT_RD:HBM4C0,CAS_COUNT_WR:HBM4C1,"                                           \
              "CAS_COUNT_RD:HBM5C0,CAS_COUNT_WR:HBM5C1,"                                           \
              "CAS_COUNT_RD:HBM6C0,CAS_COUNT_WR:HBM6C1,"                                           \
              "CAS_COUNT_RD:HBM7C0,CAS_COUNT_WR:HBM7C1"

#define HBM16_EVENTS                                                                               \
  HBM8_EVENTS ","                                                                                  \
              "CAS_COUNT_RD:HBM8C0,CAS_COUNT_WR:HBM8C1,"                                           \
              "CAS_COUNT_RD:HBM9C0,CAS_COUNT_WR:HBM9C1,"                                           \
              "CAS_COUNT_RD:HBM10C0,CAS_COUNT_WR:HBM10C1,"                                         \
              "CAS_COUNT_RD:HBM11C0,CAS_COUNT_WR:HBM11C1,"                                         \
              "CAS_COUNT_RD:HBM12C0,CAS_COUNT_WR:HBM12C1,"                                         \
              "CAS_COUNT_RD:HBM13C0,CAS_COUNT_WR:HBM13C1,"                                         \
              "CAS_COUNT_RD:HBM14C0,CAS_COUNT_WR:HBM14C1,"                                         \
              "CAS_COUNT_RD:HBM15C0,CAS_COUNT_WR:HBM15C1"

#define SPR_DDR_ONLY_EVENTS MBOX12_IMC_EVENTS
#define SPR_HBM_ONLY_EVENTS HBM16_EVENTS
#define SPR_DDR_HBM_EVENTS MBOX12_IMC_EVENTS "," HBM16_EVENTS

/* GNR/SRF LIKWID tables rename CAS to CAS_COUNT_SCH0_* (no plain CAS_COUNT_RD). */
#define MBOX12_SCH0_IMC_EVENTS                                                                     \
  "CAS_COUNT_SCH0_RD:MBOX0C0,CAS_COUNT_SCH0_WR:MBOX0C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX1C0,CAS_COUNT_SCH0_WR:MBOX1C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX2C0,CAS_COUNT_SCH0_WR:MBOX2C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX3C0,CAS_COUNT_SCH0_WR:MBOX3C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX4C0,CAS_COUNT_SCH0_WR:MBOX4C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX5C0,CAS_COUNT_SCH0_WR:MBOX5C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX6C0,CAS_COUNT_SCH0_WR:MBOX6C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX7C0,CAS_COUNT_SCH0_WR:MBOX7C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX8C0,CAS_COUNT_SCH0_WR:MBOX8C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX9C0,CAS_COUNT_SCH0_WR:MBOX9C1,"                                           \
  "CAS_COUNT_SCH0_RD:MBOX10C0,CAS_COUNT_SCH0_WR:MBOX10C1,"                                         \
  "CAS_COUNT_SCH0_RD:MBOX11C0,CAS_COUNT_SCH0_WR:MBOX11C1"

/* Default 8-CBOX eventsets (tests + profile_eventset); begin uses likwid_cha_build_eventset. */
#define CHA_SKX_CBOX_EVENTS                                                                        \
  "LLC_LOOKUP_DATA_READ:CBOX0C0:STATE=0x1F,LLC_LOOKUP_DATA_READ:CBOX1C0:STATE=0x1F,"               \
  "LLC_LOOKUP_DATA_READ:CBOX2C0:STATE=0x1F,LLC_LOOKUP_DATA_READ:CBOX3C0:STATE=0x1F,"               \
  "LLC_LOOKUP_DATA_READ:CBOX4C0:STATE=0x1F,LLC_LOOKUP_DATA_READ:CBOX5C0:STATE=0x1F,"               \
  "LLC_LOOKUP_DATA_READ:CBOX6C0:STATE=0x1F,LLC_LOOKUP_DATA_READ:CBOX7C0:STATE=0x1F,"               \
  "LLC_VICTIMS_M_STATE:CBOX0C1,LLC_VICTIMS_M_STATE:CBOX1C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX2C1,LLC_VICTIMS_M_STATE:CBOX3C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX4C1,LLC_VICTIMS_M_STATE:CBOX5C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX6C1,LLC_VICTIMS_M_STATE:CBOX7C1,"                                       \
  "LLC_LOOKUP_WRITE:CBOX0C2:STATE=0x1F,LLC_LOOKUP_WRITE:CBOX1C2:STATE=0x1F,"                       \
  "LLC_LOOKUP_WRITE:CBOX2C2:STATE=0x1F,LLC_LOOKUP_WRITE:CBOX3C2:STATE=0x1F,"                       \
  "LLC_LOOKUP_WRITE:CBOX4C2:STATE=0x1F,LLC_LOOKUP_WRITE:CBOX5C2:STATE=0x1F,"                       \
  "LLC_LOOKUP_WRITE:CBOX6C2:STATE=0x1F,LLC_LOOKUP_WRITE:CBOX7C2:STATE=0x1F,"                       \
  "BYPASS_CHA_IMC_TAKEN:CBOX0C3,BYPASS_CHA_IMC_TAKEN:CBOX1C3,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX2C3,BYPASS_CHA_IMC_TAKEN:CBOX3C3,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX4C3,BYPASS_CHA_IMC_TAKEN:CBOX5C3,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX6C3,BYPASS_CHA_IMC_TAKEN:CBOX7C3"

#define CHA_ICX_CBOX_EVENTS                                                                        \
  "LLC_LOOKUP_DATA_READ:CBOX0C0,LLC_LOOKUP_DATA_READ:CBOX1C0,"                                     \
  "LLC_LOOKUP_DATA_READ:CBOX2C0,LLC_LOOKUP_DATA_READ:CBOX3C0,"                                     \
  "LLC_LOOKUP_DATA_READ:CBOX4C0,LLC_LOOKUP_DATA_READ:CBOX5C0,"                                     \
  "LLC_LOOKUP_DATA_READ:CBOX6C0,LLC_LOOKUP_DATA_READ:CBOX7C0,"                                     \
  "LLC_VICTIMS_M_STATE:CBOX0C1,LLC_VICTIMS_M_STATE:CBOX1C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX2C1,LLC_VICTIMS_M_STATE:CBOX3C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX4C1,LLC_VICTIMS_M_STATE:CBOX5C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX6C1,LLC_VICTIMS_M_STATE:CBOX7C1,"                                       \
  "LLC_LOOKUP_WRITES_AND_OTHER:CBOX0C2,LLC_LOOKUP_WRITES_AND_OTHER:CBOX1C2,"                       \
  "LLC_LOOKUP_WRITES_AND_OTHER:CBOX2C2,LLC_LOOKUP_WRITES_AND_OTHER:CBOX3C2,"                       \
  "LLC_LOOKUP_WRITES_AND_OTHER:CBOX4C2,LLC_LOOKUP_WRITES_AND_OTHER:CBOX5C2,"                       \
  "LLC_LOOKUP_WRITES_AND_OTHER:CBOX6C2,LLC_LOOKUP_WRITES_AND_OTHER:CBOX7C2,"                       \
  "BYPASS_CHA_IMC_TAKEN:CBOX0C3,BYPASS_CHA_IMC_TAKEN:CBOX1C3,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX2C3,BYPASS_CHA_IMC_TAKEN:CBOX3C3,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX4C3,BYPASS_CHA_IMC_TAKEN:CBOX5C3,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX6C3,BYPASS_CHA_IMC_TAKEN:CBOX7C3"

#define CHA_SPR_CBOX_EVENTS                                                                        \
  "LLC_LOOKUP_DATA_RD:CBOX0C0,LLC_LOOKUP_DATA_RD:CBOX1C0,"                                         \
  "LLC_LOOKUP_DATA_RD:CBOX2C0,LLC_LOOKUP_DATA_RD:CBOX3C0,"                                         \
  "LLC_LOOKUP_DATA_RD:CBOX4C0,LLC_LOOKUP_DATA_RD:CBOX5C0,"                                         \
  "LLC_LOOKUP_DATA_RD:CBOX6C0,LLC_LOOKUP_DATA_RD:CBOX7C0,"                                         \
  "LLC_VICTIMS_M_STATE:CBOX0C1,LLC_VICTIMS_M_STATE:CBOX1C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX2C1,LLC_VICTIMS_M_STATE:CBOX3C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX4C1,LLC_VICTIMS_M_STATE:CBOX5C1,"                                       \
  "LLC_VICTIMS_M_STATE:CBOX6C1,LLC_VICTIMS_M_STATE:CBOX7C1,"                                       \
  "BYPASS_CHA_IMC_TAKEN:CBOX0C2,BYPASS_CHA_IMC_TAKEN:CBOX1C2,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX2C2,BYPASS_CHA_IMC_TAKEN:CBOX3C2,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX4C2,BYPASS_CHA_IMC_TAKEN:CBOX5C2,"                                     \
  "BYPASS_CHA_IMC_TAKEN:CBOX6C2,BYPASS_CHA_IMC_TAKEN:CBOX7C2"

#define CHA_GNR_CBOX_EVENTS                                                                        \
  "REQUESTS_READS:CBOX0C0,REQUESTS_READS:CBOX1C0,"                                                 \
  "REQUESTS_READS:CBOX2C0,REQUESTS_READS:CBOX3C0,"                                                 \
  "REQUESTS_READS:CBOX4C0,REQUESTS_READS:CBOX5C0,"                                                 \
  "REQUESTS_READS:CBOX6C0,REQUESTS_READS:CBOX7C0,"                                                 \
  "LLC_VICTIMS_LOCAL_M:CBOX0C1,LLC_VICTIMS_LOCAL_M:CBOX1C1,"                                       \
  "LLC_VICTIMS_LOCAL_M:CBOX2C1,LLC_VICTIMS_LOCAL_M:CBOX3C1,"                                       \
  "LLC_VICTIMS_LOCAL_M:CBOX4C1,LLC_VICTIMS_LOCAL_M:CBOX5C1,"                                       \
  "LLC_VICTIMS_LOCAL_M:CBOX6C1,LLC_VICTIMS_LOCAL_M:CBOX7C1"

/* LIKWID Zen2 MEM: only DRAM_CHANNEL_0/1 on DFC. */
#define DF_ROME_EVENTS "DRAM_CHANNEL_0:DFC0,DRAM_CHANNEL_1:DFC1"

/* LIKWID Zen3 MEM1: DRAM_CHANNEL_0–3 on DFC. */
#define DF_MILAN_EVENTS                                                                            \
  "DRAM_CHANNEL_0:DFC0,DRAM_CHANNEL_1:DFC1,DRAM_CHANNEL_2:DFC2,DRAM_CHANNEL_3:DFC3"

/* LIKWID Zen4: DRAM_READS_LOCAL_CHANNEL_* on DFC (PPR-aligned encodings). */
#define DF_GENOA_EVENTS                                                                            \
  "DRAM_READS_LOCAL_CHANNEL_0:DFC0,DRAM_READS_LOCAL_CHANNEL_1:DFC1,"                               \
  "DRAM_READS_LOCAL_CHANNEL_2:DFC2,DRAM_READS_LOCAL_CHANNEL_3:DFC3"

/* LIKWID Zen5 MEM: UMC CAS reads (first four channels) → dram_chan*_bytes. */
#define DF_TURIN_EVENTS "CAS_CMD_RD:UMC0C0,CAS_CMD_RD:UMC1C0,CAS_CMD_RD:UMC2C0,CAS_CMD_RD:UMC3C0"

static const char *const profile_events[LIKWID_UNCORE_PROFILE_COUNT] = {
    [LIKWID_UNCORE_PROFILE_IMC_SKX] = MBOX6_IMC_EVENTS,
    /* Stampede3 ICX PERF: cas_count_* on uncore_imc_0..11 — not DDR_*:MDEV*. */
    [LIKWID_UNCORE_PROFILE_IMC_ICX] = MBOX12_IMC_EVENTS,
    [LIKWID_UNCORE_PROFILE_IMC_SPR] = SPR_DDR_HBM_EVENTS,
    [LIKWID_UNCORE_PROFILE_IMC_EMR] = SPR_DDR_HBM_EVENTS,
    [LIKWID_UNCORE_PROFILE_IMC_GNR] = MBOX12_SCH0_IMC_EVENTS,
    [LIKWID_UNCORE_PROFILE_IMC_SRF] = MBOX12_SCH0_IMC_EVENTS,
    [LIKWID_UNCORE_PROFILE_CHA_SKX] = CHA_SKX_CBOX_EVENTS,
    [LIKWID_UNCORE_PROFILE_CHA_ICX] = CHA_ICX_CBOX_EVENTS,
    [LIKWID_UNCORE_PROFILE_CHA_SPR] = CHA_SPR_CBOX_EVENTS,
    [LIKWID_UNCORE_PROFILE_CHA_EMR] = CHA_SPR_CBOX_EVENTS,
    [LIKWID_UNCORE_PROFILE_CHA_GNR] = CHA_GNR_CBOX_EVENTS,
    [LIKWID_UNCORE_PROFILE_DF_ROME] = DF_ROME_EVENTS,
    [LIKWID_UNCORE_PROFILE_DF_MILAN] = DF_MILAN_EVENTS,
    [LIKWID_UNCORE_PROFILE_DF_GENOA] = DF_GENOA_EVENTS,
    [LIKWID_UNCORE_PROFILE_DF_TURIN] = DF_TURIN_EVENTS,
};

int likwid_uncore_profile_matches_processor(likwid_uncore_profile_t profile, processor_t p)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_IMC_SKX:
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
    return intel_processor_is_skx_server(p);
  case LIKWID_UNCORE_PROFILE_IMC_ICX:
  case LIKWID_UNCORE_PROFILE_CHA_ICX:
    return intel_processor_is_icx(p);
  case LIKWID_UNCORE_PROFILE_IMC_SPR:
  case LIKWID_UNCORE_PROFILE_CHA_SPR:
    return intel_processor_is_spr(p);
  case LIKWID_UNCORE_PROFILE_IMC_EMR:
  case LIKWID_UNCORE_PROFILE_CHA_EMR:
    return intel_processor_is_emr(p);
  case LIKWID_UNCORE_PROFILE_IMC_GNR:
  case LIKWID_UNCORE_PROFILE_CHA_GNR:
    return intel_processor_is_gnr(p);
  case LIKWID_UNCORE_PROFILE_IMC_SRF:
    return intel_processor_is_srf(p);
  case LIKWID_UNCORE_PROFILE_DF_ROME:
    return amd_processor_is_rome(p);
  case LIKWID_UNCORE_PROFILE_DF_MILAN:
    return amd_processor_is_milan(p);
  case LIKWID_UNCORE_PROFILE_DF_GENOA:
    return amd_processor_is_genoa(p);
  case LIKWID_UNCORE_PROFILE_DF_TURIN:
    return amd_processor_is_turin(p);
  default:
    return 0;
  }
}

const char *likwid_uncore_profile_eventset(likwid_uncore_profile_t profile)
{
  if (profile < 0 || profile >= LIKWID_UNCORE_PROFILE_COUNT)
    return NULL;
  return profile_events[profile];
}

const char *likwid_spr_imc_eventset_string(likwid_spr_imc_eventset_t variant)
{
  switch (variant) {
  case LIKWID_SPR_IMC_EVT_DDR_HBM:
    return SPR_DDR_HBM_EVENTS;
  case LIKWID_SPR_IMC_EVT_DDR_ONLY:
    return SPR_DDR_ONLY_EVENTS;
  case LIKWID_SPR_IMC_EVT_HBM_ONLY:
    return SPR_HBM_ONLY_EVENTS;
  default:
    return NULL;
  }
}

static void spr_imc_try_append(likwid_spr_imc_eventset_t *out, int *n, int cap,
                               likwid_spr_imc_eventset_t variant)
{
  int i;

  if (out == NULL || n == NULL || *n >= cap)
    return;
  for (i = 0; i < *n; i++) {
    if (out[i] == variant)
      return;
  }
  out[*n] = variant;
  (*n)++;
}

/*
 * Always prefer DDR+HBM first. Stampede3 SPR EDAC often labels only DDR (or
 * reports empty has_* when dimm_mem_speed is missing); EDAC must not demote
 * primary to DDR_ONLY and skip HBM PMU programming.
 */
int likwid_spr_imc_eventset_try_order(int has_ddr, int has_hbm, likwid_spr_imc_eventset_t *out,
                                      int out_cap)
{
  int n = 0;

  (void)has_ddr;
  (void)has_hbm;

  if (out == NULL || out_cap <= 0)
    return 0;

  spr_imc_try_append(out, &n, out_cap, LIKWID_SPR_IMC_EVT_DDR_HBM);
  spr_imc_try_append(out, &n, out_cap, LIKWID_SPR_IMC_EVT_DDR_ONLY);
  spr_imc_try_append(out, &n, out_cap, LIKWID_SPR_IMC_EVT_HBM_ONLY);
  return n;
}

const char *likwid_spr_imc_eventset_variant_name(likwid_spr_imc_eventset_t variant)
{
  switch (variant) {
  case LIKWID_SPR_IMC_EVT_DDR_HBM:
    return "DDR_HBM";
  case LIKWID_SPR_IMC_EVT_DDR_ONLY:
    return "DDR_ONLY";
  case LIKWID_SPR_IMC_EVT_HBM_ONLY:
    return "HBM_ONLY";
  default:
    return "UNKNOWN";
  }
}

/* HBM size ladder after HBM_ONLY(16): 8, 4, 1 channels. */
const char *likwid_spr_imc_hbm_channels_eventset(int n_channels)
{
  if (n_channels >= 16)
    return HBM16_EVENTS;
  if (n_channels >= 8)
    return HBM8_EVENTS;
  if (n_channels >= 4)
    return HBM4_EVENTS;
  if (n_channels >= 1)
    return HBM1_EVENTS;
  return NULL;
}

int likwid_spr_imc_hbm_ladder_sizes(int *out, int out_cap)
{
  static const int sizes[] = {8, 4, 1};
  int n = 0;
  int i;

  if (out == NULL || out_cap <= 0)
    return 0;
  for (i = 0; i < (int)(sizeof(sizes) / sizeof(sizes[0])) && n < out_cap; i++)
    out[n++] = sizes[i];
  return n;
}

const char *likwid_icx_imc_eventset_string(likwid_icx_imc_eventset_t variant)
{
  switch (variant) {
  case LIKWID_ICX_IMC_EVT_MBOX12:
    return MBOX12_IMC_EVENTS;
  case LIKWID_ICX_IMC_EVT_MBOX6:
    return MBOX6_IMC_EVENTS;
  case LIKWID_ICX_IMC_EVT_MBOX4:
    return MBOX4_IMC_EVENTS;
  default:
    return NULL;
  }
}

const char *likwid_icx_imc_eventset_variant_name(likwid_icx_imc_eventset_t variant)
{
  switch (variant) {
  case LIKWID_ICX_IMC_EVT_MBOX12:
    return "MBOX12";
  case LIKWID_ICX_IMC_EVT_MBOX6:
    return "MBOX6";
  case LIKWID_ICX_IMC_EVT_MBOX4:
    return "MBOX4";
  default:
    return "UNKNOWN";
  }
}

int likwid_icx_imc_eventset_try_order(likwid_icx_imc_eventset_t *out, int out_cap)
{
  static const likwid_icx_imc_eventset_t order[] = {
      LIKWID_ICX_IMC_EVT_MBOX12, LIKWID_ICX_IMC_EVT_MBOX6, LIKWID_ICX_IMC_EVT_MBOX4};
  int n = 0;
  int i;

  if (out == NULL || out_cap <= 0)
    return 0;
  for (i = 0; i < (int)(sizeof(order) / sizeof(order[0])) && n < out_cap; i++)
    out[n++] = order[i];
  return n;
}

static const char *counter_name_base(const char *counter_name, char *work, size_t work_len)
{
  const char *state;

  if (counter_name == NULL)
    return NULL;
  state = strstr(counter_name, ":STATE=");
  if (state == NULL)
    return counter_name;
  if ((size_t)(state - counter_name) >= work_len)
    return counter_name;
  memcpy(work, counter_name, (size_t)(state - counter_name));
  work[state - counter_name] = '\0';
  return work;
}

/* icx_mbox_as_mdev: ICX keeps historical device names mdevN while programming MBOX*. */
static int map_mbox_hbm_mdev(const char *counter_name, char *dev_out, size_t dev_len,
                             const char **key_out, int icx_mbox_as_mdev)
{
  unsigned int idx = 0;
  char ch[4];
  char work[128];
  const char *base = counter_name_base(counter_name, work, sizeof(work));

  if (base == NULL || dev_out == NULL || key_out == NULL)
    return -1;

  if (sscanf(base, "MBOX%uC%1s", &idx, ch) == 2) {
    if (icx_mbox_as_mdev)
      snprintf(dev_out, dev_len, "mdev%u", idx);
    else
      snprintf(dev_out, dev_len, "mbox%u", idx);
    if (strcmp(ch, "0") == 0)
      *key_out = "dram_cas_reads";
    else
      *key_out = "dram_cas_writes";
    return 0;
  }
  if (sscanf(base, "HBM%uC%1s", &idx, ch) == 2) {
    snprintf(dev_out, dev_len, "hbm%u", idx);
    if (strcmp(ch, "0") == 0)
      *key_out = "hbm_cas_reads";
    else
      *key_out = "hbm_cas_writes";
    return 0;
  }
  if (sscanf(base, "MDEV%uC%1s", &idx, ch) == 2) {
    snprintf(dev_out, dev_len, "mdev%u", idx);
    if (strcmp(ch, "0") == 0)
      *key_out = "dram_cas_reads";
    else
      *key_out = "dram_cas_writes";
    return 0;
  }
  return -1;
}

static int map_cbox(likwid_uncore_profile_t profile, const char *counter_name, char *dev_out,
                    size_t dev_len, const char **key_out)
{
  unsigned int idx = 0;
  char ch[4];
  char work[128];
  const char *base = counter_name_base(counter_name, work, sizeof(work));

  if (base == NULL || dev_out == NULL || key_out == NULL)
    return -1;

  if (sscanf(base, "CBOX%uC%1s", &idx, ch) != 2)
    return -1;

  snprintf(dev_out, dev_len, "cbox%u", idx);
  if (strcmp(ch, "0") == 0) {
    *key_out = "llc_lookup_data_read_local";
    return 0;
  }
  if (strcmp(ch, "1") == 0) {
    *key_out = "sf_evictions_mes";
    return 0;
  }
  if (strcmp(ch, "2") == 0) {
    /* SKX/ICX: write on C2; SPR/EMR: bypass on C2 (no write event). */
    if (profile == LIKWID_UNCORE_PROFILE_CHA_SPR || profile == LIKWID_UNCORE_PROFILE_CHA_EMR)
      *key_out = "bypass_cha_imc_all";
    else if (profile == LIKWID_UNCORE_PROFILE_CHA_SKX || profile == LIKWID_UNCORE_PROFILE_CHA_ICX)
      *key_out = "llc_lookup_write";
    else
      return -1;
    return 0;
  }
  if (strcmp(ch, "3") == 0) {
    if (profile == LIKWID_UNCORE_PROFILE_CHA_SKX || profile == LIKWID_UNCORE_PROFILE_CHA_ICX) {
      *key_out = "bypass_cha_imc_all";
      return 0;
    }
    return -1;
  }
  return -1;
}

static int map_amd_df(const char *counter_name, char *dev_out, size_t dev_len, const char **key_out)
{
  unsigned int idx = 0;
  char work[128];
  const char *base = counter_name_base(counter_name, work, sizeof(work));

  if (base == NULL || dev_out == NULL || key_out == NULL)
    return -1;

  snprintf(dev_out, dev_len, "df");

  if (sscanf(base, "DFC%u", &idx) == 1) {
    static const char *const dfc_keys[] = {"dram_chan0_bytes", "dram_chan1_bytes",
                                           "dram_chan2_bytes", "dram_chan3_bytes"};
    if (idx >= 4)
      return -1;
    *key_out = dfc_keys[idx];
    return 0;
  }

  /* Turin UMC CAS_CMD_RD on UMC{N}C0 → dram_chanN_bytes. */
  if (sscanf(base, "UMC%uC0", &idx) == 1) {
    static const char *const umc_keys[] = {"dram_chan0_bytes", "dram_chan1_bytes",
                                           "dram_chan2_bytes", "dram_chan3_bytes"};
    if (idx >= 4)
      return -1;
    *key_out = umc_keys[idx];
    return 0;
  }

  return -1;
}

int likwid_uncore_profile_map_counter(likwid_uncore_profile_t profile, const char *counter_name,
                                      char *dev_out, size_t dev_len, const char **key_out)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
  case LIKWID_UNCORE_PROFILE_CHA_ICX:
  case LIKWID_UNCORE_PROFILE_CHA_SPR:
  case LIKWID_UNCORE_PROFILE_CHA_EMR:
  case LIKWID_UNCORE_PROFILE_CHA_GNR:
    return map_cbox(profile, counter_name, dev_out, dev_len, key_out);
  case LIKWID_UNCORE_PROFILE_IMC_ICX:
    return map_mbox_hbm_mdev(counter_name, dev_out, dev_len, key_out, 1);
  case LIKWID_UNCORE_PROFILE_IMC_SKX:
  case LIKWID_UNCORE_PROFILE_IMC_SPR:
  case LIKWID_UNCORE_PROFILE_IMC_EMR:
  case LIKWID_UNCORE_PROFILE_IMC_GNR:
  case LIKWID_UNCORE_PROFILE_IMC_SRF:
    return map_mbox_hbm_mdev(counter_name, dev_out, dev_len, key_out, 0);
  case LIKWID_UNCORE_PROFILE_DF_ROME:
  case LIKWID_UNCORE_PROFILE_DF_MILAN:
  case LIKWID_UNCORE_PROFILE_DF_GENOA:
  case LIKWID_UNCORE_PROFILE_DF_TURIN:
    return map_amd_df(counter_name, dev_out, dev_len, key_out);
  default:
    return -1;
  }
}

int likwid_cha_profile_is_cha(likwid_uncore_profile_t profile)
{
  return profile == LIKWID_UNCORE_PROFILE_CHA_SKX || profile == LIKWID_UNCORE_PROFILE_CHA_ICX ||
         profile == LIKWID_UNCORE_PROFILE_CHA_SPR || profile == LIKWID_UNCORE_PROFILE_CHA_EMR ||
         profile == LIKWID_UNCORE_PROFILE_CHA_GNR;
}

int likwid_cha_profile_cbox_max(likwid_uncore_profile_t profile)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
    return 28; /* LIKWID skylakeX CBOX0–27 */
  case LIKWID_UNCORE_PROFILE_CHA_ICX:
    return 40; /* CBOX0–39 */
  case LIKWID_UNCORE_PROFILE_CHA_SPR:
  case LIKWID_UNCORE_PROFILE_CHA_EMR:
    return 60; /* CBOX0–59 */
  case LIKWID_UNCORE_PROFILE_CHA_GNR:
    return 126; /* CBOX0–125 */
  default:
    return 0;
  }
}

int likwid_cha_events_per_cbox(likwid_uncore_profile_t profile)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
  case LIKWID_UNCORE_PROFILE_CHA_ICX:
    return 4;
  case LIKWID_UNCORE_PROFILE_CHA_SPR:
  case LIKWID_UNCORE_PROFILE_CHA_EMR:
    return 3;
  case LIKWID_UNCORE_PROFILE_CHA_GNR:
    return 2;
  default:
    return 0;
  }
}

static int cha_format_one_event(likwid_uncore_profile_t profile, unsigned int cbox, int ctr,
                                char *out, size_t cap)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
    if (ctr == 0)
      return snprintf(out, cap, "LLC_LOOKUP_DATA_READ:CBOX%uC0:STATE=0x1F", cbox);
    if (ctr == 1)
      return snprintf(out, cap, "LLC_VICTIMS_M_STATE:CBOX%uC1", cbox);
    if (ctr == 2)
      return snprintf(out, cap, "LLC_LOOKUP_WRITE:CBOX%uC2:STATE=0x1F", cbox);
    if (ctr == 3)
      return snprintf(out, cap, "BYPASS_CHA_IMC_TAKEN:CBOX%uC3", cbox);
    break;
  case LIKWID_UNCORE_PROFILE_CHA_ICX:
    if (ctr == 0)
      return snprintf(out, cap, "LLC_LOOKUP_DATA_READ:CBOX%uC0", cbox);
    if (ctr == 1)
      return snprintf(out, cap, "LLC_VICTIMS_M_STATE:CBOX%uC1", cbox);
    if (ctr == 2)
      return snprintf(out, cap, "LLC_LOOKUP_WRITES_AND_OTHER:CBOX%uC2", cbox);
    if (ctr == 3)
      return snprintf(out, cap, "BYPASS_CHA_IMC_TAKEN:CBOX%uC3", cbox);
    break;
  case LIKWID_UNCORE_PROFILE_CHA_SPR:
  case LIKWID_UNCORE_PROFILE_CHA_EMR:
    if (ctr == 0)
      return snprintf(out, cap, "LLC_LOOKUP_DATA_RD:CBOX%uC0", cbox);
    if (ctr == 1)
      return snprintf(out, cap, "LLC_VICTIMS_M_STATE:CBOX%uC1", cbox);
    if (ctr == 2)
      return snprintf(out, cap, "BYPASS_CHA_IMC_TAKEN:CBOX%uC2", cbox);
    break;
  case LIKWID_UNCORE_PROFILE_CHA_GNR:
    if (ctr == 0)
      return snprintf(out, cap, "REQUESTS_READS:CBOX%uC0", cbox);
    if (ctr == 1)
      return snprintf(out, cap, "LLC_VICTIMS_LOCAL_M:CBOX%uC1", cbox);
    break;
  default:
    break;
  }
  return -1;
}

int likwid_cha_build_eventset(likwid_uncore_profile_t profile, int n_cbox, char *buf,
                              size_t buf_len)
{
  int per;
  int i;
  int ctr;
  size_t used = 0;
  int first = 1;

  if (buf == NULL || buf_len == 0 || n_cbox <= 0)
    return -1;
  per = likwid_cha_events_per_cbox(profile);
  if (per <= 0)
    return -1;
  if (n_cbox > likwid_cha_profile_cbox_max(profile))
    n_cbox = likwid_cha_profile_cbox_max(profile);

  buf[0] = '\0';
  for (i = 0; i < n_cbox; i++) {
    for (ctr = 0; ctr < per; ctr++) {
      char one[96];
      int n;

      n = cha_format_one_event(profile, (unsigned int)i, ctr, one, sizeof(one));
      if (n < 0 || (size_t)n >= sizeof(one))
        return -1;
      if (!first) {
        if (used + 1 >= buf_len)
          return -1;
        buf[used++] = ',';
        buf[used] = '\0';
      }
      if (used + (size_t)n >= buf_len)
        return -1;
      memcpy(buf + used, one, (size_t)n + 1);
      used += (size_t)n;
      first = 0;
    }
  }
  return first ? -1 : 0;
}

int likwid_cha_ladder_sizes(int discovered, int table_max, int *out, int out_cap)
{
  static const int fallbacks[] = {28, 16, 8};
  int primary;
  int n = 0;
  int i;
  int f;

  if (out == NULL || out_cap <= 0 || table_max <= 0)
    return 0;

  if (discovered <= 0)
    primary = 8;
  else
    primary = discovered;
  if (primary > table_max)
    primary = table_max;
  if (primary < 1)
    primary = 1;

  out[n++] = primary;
  for (f = 0; f < (int)(sizeof(fallbacks) / sizeof(fallbacks[0])) && n < out_cap; f++) {
    int sz = fallbacks[f];
    int dup = 0;

    if (sz >= primary || sz > table_max)
      continue;
    for (i = 0; i < n; i++) {
      if (out[i] == sz) {
        dup = 1;
        break;
      }
    }
    if (!dup)
      out[n++] = sz;
  }
  return n;
}

int likwid_cha_count_sysfs_devices(const char *devices_root)
{
  const char *root;
  DIR *dir;
  struct dirent *ent;
  int count = 0;
  const char *env;

  env = getenv("HPCPERFSTATS_UNCORE_DEVICES");
  if (devices_root != NULL && devices_root[0] != '\0')
    root = devices_root;
  else if (env != NULL && env[0] != '\0')
    root = env;
  else
    root = "/sys/bus/event_source/devices";

  dir = opendir(root);
  if (dir == NULL)
    return 0;
  while ((ent = readdir(dir)) != NULL) {
    /* uncore_cha_N only (not uncore_cbox_*). */
    if (strncmp(ent->d_name, "uncore_cha_", 11) != 0)
      continue;
    if (ent->d_name[11] < '0' || ent->d_name[11] > '9')
      continue;
    count++;
  }
  closedir(dir);
  return count;
}
