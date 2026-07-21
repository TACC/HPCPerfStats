#include <stdio.h>
#include <string.h>
#include "likwid_uncore_profiles.h"
#include "intel_processor.h"

#define MBOX4_IMC_EVENTS                                                                           \
  "MBOX0C0 CAS_COUNT_RD,MBOX0C1 CAS_COUNT_WR,"                                                     \
  "MBOX1C0 CAS_COUNT_RD,MBOX1C1 CAS_COUNT_WR,"                                                     \
  "MBOX2C0 CAS_COUNT_RD,MBOX2C1 CAS_COUNT_WR,"                                                     \
  "MBOX3C0 CAS_COUNT_RD,MBOX3C1 CAS_COUNT_WR"

#define MBOX6_IMC_EVENTS                                                                           \
  MBOX4_IMC_EVENTS ","                                                                             \
                   "MBOX4C0 CAS_COUNT_RD,MBOX4C1 CAS_COUNT_WR,"                                    \
                   "MBOX5C0 CAS_COUNT_RD,MBOX5C1 CAS_COUNT_WR"

/* LIKWID 5.5.2rc2 SPR counters.h defines MBOX0–11 only (not MBOX12–15). */
#define MBOX12_IMC_EVENTS                                                                          \
  MBOX6_IMC_EVENTS ","                                                                             \
                   "MBOX6C0 CAS_COUNT_RD,MBOX6C1 CAS_COUNT_WR,"                                    \
                   "MBOX7C0 CAS_COUNT_RD,MBOX7C1 CAS_COUNT_WR,"                                    \
                   "MBOX8C0 CAS_COUNT_RD,MBOX8C1 CAS_COUNT_WR,"                                    \
                   "MBOX9C0 CAS_COUNT_RD,MBOX9C1 CAS_COUNT_WR,"                                    \
                   "MBOX10C0 CAS_COUNT_RD,MBOX10C1 CAS_COUNT_WR,"                                  \
                   "MBOX11C0 CAS_COUNT_RD,MBOX11C1 CAS_COUNT_WR"

#define MDEV4_ICX_EVENTS                                                                           \
  "MDEV0C0 DDR_READ_BYTES,MDEV0C1 DDR_WRITE_BYTES,"                                                \
  "MDEV1C0 DDR_READ_BYTES,MDEV1C1 DDR_WRITE_BYTES,"                                                \
  "MDEV2C0 DDR_READ_BYTES,MDEV2C1 DDR_WRITE_BYTES,"                                                \
  "MDEV3C0 DDR_READ_BYTES,MDEV3C1 DDR_WRITE_BYTES"

#define HBM1_EVENTS "HBM0C0 CAS_COUNT_RD,HBM0C1 CAS_COUNT_WR"

#define HBM4_EVENTS                                                                                \
  HBM1_EVENTS ","                                                                                  \
              "HBM1C0 CAS_COUNT_RD,HBM1C1 CAS_COUNT_WR,"                                           \
              "HBM2C0 CAS_COUNT_RD,HBM2C1 CAS_COUNT_WR,"                                           \
              "HBM3C0 CAS_COUNT_RD,HBM3C1 CAS_COUNT_WR"

#define HBM8_EVENTS                                                                                \
  HBM4_EVENTS ","                                                                                  \
              "HBM4C0 CAS_COUNT_RD,HBM4C1 CAS_COUNT_WR,"                                           \
              "HBM5C0 CAS_COUNT_RD,HBM5C1 CAS_COUNT_WR,"                                           \
              "HBM6C0 CAS_COUNT_RD,HBM6C1 CAS_COUNT_WR,"                                           \
              "HBM7C0 CAS_COUNT_RD,HBM7C1 CAS_COUNT_WR"

#define HBM16_EVENTS                                                                               \
  HBM8_EVENTS ","                                                                                  \
              "HBM8C0 CAS_COUNT_RD,HBM8C1 CAS_COUNT_WR,"                                           \
              "HBM9C0 CAS_COUNT_RD,HBM9C1 CAS_COUNT_WR,"                                           \
              "HBM10C0 CAS_COUNT_RD,HBM10C1 CAS_COUNT_WR,"                                         \
              "HBM11C0 CAS_COUNT_RD,HBM11C1 CAS_COUNT_WR,"                                         \
              "HBM12C0 CAS_COUNT_RD,HBM12C1 CAS_COUNT_WR,"                                         \
              "HBM13C0 CAS_COUNT_RD,HBM13C1 CAS_COUNT_WR,"                                         \
              "HBM14C0 CAS_COUNT_RD,HBM14C1 CAS_COUNT_WR,"                                         \
              "HBM15C0 CAS_COUNT_RD,HBM15C1 CAS_COUNT_WR"

#define SPR_DDR_ONLY_EVENTS MBOX12_IMC_EVENTS
#define SPR_HBM_ONLY_EVENTS HBM16_EVENTS
#define SPR_DDR_HBM_EVENTS MBOX12_IMC_EVENTS "," HBM16_EVENTS

#define CHA_SKX_CBOX_EVENTS                                                                        \
  "CBOX0C0 LLC_LOOKUP_DATA_READ,CBOX1C0 LLC_LOOKUP_DATA_READ,"                                     \
  "CBOX2C0 LLC_LOOKUP_DATA_READ,CBOX3C0 LLC_LOOKUP_DATA_READ,"                                     \
  "CBOX4C0 LLC_LOOKUP_DATA_READ,CBOX5C0 LLC_LOOKUP_DATA_READ,"                                     \
  "CBOX6C0 LLC_LOOKUP_DATA_READ,CBOX7C0 LLC_LOOKUP_DATA_READ,"                                     \
  "CBOX0C1 LLC_VICTIMS_M_STATE,CBOX1C1 LLC_VICTIMS_M_STATE,"                                       \
  "CBOX2C1 LLC_VICTIMS_M_STATE,CBOX3C1 LLC_VICTIMS_M_STATE,"                                       \
  "CBOX4C1 LLC_VICTIMS_M_STATE,CBOX5C1 LLC_VICTIMS_M_STATE,"                                       \
  "CBOX6C1 LLC_VICTIMS_M_STATE,CBOX7C1 LLC_VICTIMS_M_STATE"

static const char *const profile_events[LIKWID_UNCORE_PROFILE_COUNT] = {
    [LIKWID_UNCORE_PROFILE_IMC_SKX] = MBOX6_IMC_EVENTS,
    [LIKWID_UNCORE_PROFILE_IMC_ICX] = MDEV4_ICX_EVENTS,
    [LIKWID_UNCORE_PROFILE_IMC_SPR] = SPR_DDR_HBM_EVENTS,
    [LIKWID_UNCORE_PROFILE_CHA_SKX] = CHA_SKX_CBOX_EVENTS,
};

int likwid_uncore_profile_matches_processor(likwid_uncore_profile_t profile, processor_t p)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_IMC_SKX:
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
    return intel_processor_is_skx_server(p);
  case LIKWID_UNCORE_PROFILE_IMC_ICX:
    return intel_processor_is_icx(p);
  case LIKWID_UNCORE_PROFILE_IMC_SPR:
    return intel_processor_is_spr(p);
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

static int map_mbox_hbm_mdev(const char *counter_name, char *dev_out, size_t dev_len,
                             const char **key_out)
{
  unsigned int idx = 0;
  char ch[4];
  char work[128];
  const char *base = counter_name_base(counter_name, work, sizeof(work));

  if (base == NULL || dev_out == NULL || key_out == NULL)
    return -1;

  if (sscanf(base, "MBOX%uC%1s", &idx, ch) == 2) {
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

static int map_cbox(const char *counter_name, char *dev_out, size_t dev_len, const char **key_out)
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
  if (strcmp(ch, "0") == 0)
    *key_out = "llc_lookup_data_read_local";
  else if (strcmp(ch, "1") == 0)
    *key_out = "sf_evictions_mes";
  else
    return -1;
  return 0;
}

int likwid_uncore_profile_map_counter(likwid_uncore_profile_t profile, const char *counter_name,
                                      char *dev_out, size_t dev_len, const char **key_out)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
    return map_cbox(counter_name, dev_out, dev_len, key_out);
  case LIKWID_UNCORE_PROFILE_IMC_SKX:
  case LIKWID_UNCORE_PROFILE_IMC_ICX:
  case LIKWID_UNCORE_PROFILE_IMC_SPR:
    return map_mbox_hbm_mdev(counter_name, dev_out, dev_len, key_out);
  default:
    return -1;
  }
}
