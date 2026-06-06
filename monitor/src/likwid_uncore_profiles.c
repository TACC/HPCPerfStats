#include <stdio.h>
#include <string.h>
#include "likwid_uncore_profiles.h"
#include "intel_processor.h"

#define MBOX4_IMC_EVENTS \
  "MBOX0C0 CAS_COUNT_RD,MBOX0C1 CAS_COUNT_WR," \
  "MBOX1C0 CAS_COUNT_RD,MBOX1C1 CAS_COUNT_WR," \
  "MBOX2C0 CAS_COUNT_RD,MBOX2C1 CAS_COUNT_WR," \
  "MBOX3C0 CAS_COUNT_RD,MBOX3C1 CAS_COUNT_WR"

#define MBOX6_IMC_EVENTS \
  MBOX4_IMC_EVENTS "," \
  "MBOX4C0 CAS_COUNT_RD,MBOX4C1 CAS_COUNT_WR," \
  "MBOX5C0 CAS_COUNT_RD,MBOX5C1 CAS_COUNT_WR"

#define MBOX16_IMC_EVENTS \
  MBOX6_IMC_EVENTS "," \
  "MBOX6C0 CAS_COUNT_RD,MBOX6C1 CAS_COUNT_WR," \
  "MBOX7C0 CAS_COUNT_RD,MBOX7C1 CAS_COUNT_WR," \
  "MBOX8C0 CAS_COUNT_RD,MBOX8C1 CAS_COUNT_WR," \
  "MBOX9C0 CAS_COUNT_RD,MBOX9C1 CAS_COUNT_WR," \
  "MBOX10C0 CAS_COUNT_RD,MBOX10C1 CAS_COUNT_WR," \
  "MBOX11C0 CAS_COUNT_RD,MBOX11C1 CAS_COUNT_WR," \
  "MBOX12C0 CAS_COUNT_RD,MBOX12C1 CAS_COUNT_WR," \
  "MBOX13C0 CAS_COUNT_RD,MBOX13C1 CAS_COUNT_WR," \
  "MBOX14C0 CAS_COUNT_RD,MBOX14C1 CAS_COUNT_WR," \
  "MBOX15C0 CAS_COUNT_RD,MBOX15C1 CAS_COUNT_WR"

#define MDEV4_ICX_EVENTS \
  "MDEV0C0 DDR_READ_BYTES,MDEV0C1 DDR_WRITE_BYTES," \
  "MDEV1C0 DDR_READ_BYTES,MDEV1C1 DDR_WRITE_BYTES," \
  "MDEV2C0 DDR_READ_BYTES,MDEV2C1 DDR_WRITE_BYTES," \
  "MDEV3C0 DDR_READ_BYTES,MDEV3C1 DDR_WRITE_BYTES"

#define HBM16_EVENTS \
  "HBM0C0 CAS_COUNT_RD,HBM0C1 CAS_COUNT_WR," \
  "HBM1C0 CAS_COUNT_RD,HBM1C1 CAS_COUNT_WR," \
  "HBM2C0 CAS_COUNT_RD,HBM2C1 CAS_COUNT_WR," \
  "HBM3C0 CAS_COUNT_RD,HBM3C1 CAS_COUNT_WR," \
  "HBM4C0 CAS_COUNT_RD,HBM4C1 CAS_COUNT_WR," \
  "HBM5C0 CAS_COUNT_RD,HBM5C1 CAS_COUNT_WR," \
  "HBM6C0 CAS_COUNT_RD,HBM6C1 CAS_COUNT_WR," \
  "HBM7C0 CAS_COUNT_RD,HBM7C1 CAS_COUNT_WR," \
  "HBM8C0 CAS_COUNT_RD,HBM8C1 CAS_COUNT_WR," \
  "HBM9C0 CAS_COUNT_RD,HBM9C1 CAS_COUNT_WR," \
  "HBM10C0 CAS_COUNT_RD,HBM10C1 CAS_COUNT_WR," \
  "HBM11C0 CAS_COUNT_RD,HBM11C1 CAS_COUNT_WR," \
  "HBM12C0 CAS_COUNT_RD,HBM12C1 CAS_COUNT_WR," \
  "HBM13C0 CAS_COUNT_RD,HBM13C1 CAS_COUNT_WR," \
  "HBM14C0 CAS_COUNT_RD,HBM14C1 CAS_COUNT_WR," \
  "HBM15C0 CAS_COUNT_RD,HBM15C1 CAS_COUNT_WR"

#define SPR_DDR_HBM_EVENTS MBOX16_IMC_EVENTS "," HBM16_EVENTS

static const char *const profile_events[LIKWID_UNCORE_PROFILE_COUNT] = {
  [LIKWID_UNCORE_PROFILE_IMC_SNB] = MBOX4_IMC_EVENTS,
  [LIKWID_UNCORE_PROFILE_IMC_IVB] = MBOX4_IMC_EVENTS,
  [LIKWID_UNCORE_PROFILE_IMC_HSW] = MBOX4_IMC_EVENTS,
  [LIKWID_UNCORE_PROFILE_IMC_BDW] = MBOX4_IMC_EVENTS,
  [LIKWID_UNCORE_PROFILE_IMC_SKX] = MBOX6_IMC_EVENTS,
  [LIKWID_UNCORE_PROFILE_IMC_ICX] = MDEV4_ICX_EVENTS,
  [LIKWID_UNCORE_PROFILE_IMC_SPR] = SPR_DDR_HBM_EVENTS,
  [LIKWID_UNCORE_PROFILE_CBO_SNB] = NULL,
  [LIKWID_UNCORE_PROFILE_CBO_IVB] = NULL,
  [LIKWID_UNCORE_PROFILE_CBO_HSW] = NULL,
  [LIKWID_UNCORE_PROFILE_CBO_BDW] = NULL,
  [LIKWID_UNCORE_PROFILE_CHA_SKX] = NULL,
  [LIKWID_UNCORE_PROFILE_QPI_SNB] = NULL,
  [LIKWID_UNCORE_PROFILE_QPI_IVB] = NULL,
  [LIKWID_UNCORE_PROFILE_QPI_HSW] = NULL,
  [LIKWID_UNCORE_PROFILE_QPI_BDW] = NULL,
  [LIKWID_UNCORE_PROFILE_HAU_SNB] = NULL,
  [LIKWID_UNCORE_PROFILE_HAU_IVB] = NULL,
  [LIKWID_UNCORE_PROFILE_HAU_HSW] = NULL,
  [LIKWID_UNCORE_PROFILE_HAU_BDW] = NULL,
  [LIKWID_UNCORE_PROFILE_R2PCI_SNB] = NULL,
  [LIKWID_UNCORE_PROFILE_R2PCI_IVB] = NULL,
  [LIKWID_UNCORE_PROFILE_R2PCI_HSW] = NULL,
  [LIKWID_UNCORE_PROFILE_R2PCI_BDW] = NULL,
};

int likwid_uncore_profile_matches_processor(likwid_uncore_profile_t profile,
                                            processor_t p)
{
  switch (profile) {
  case LIKWID_UNCORE_PROFILE_IMC_SNB:
    return intel_processor_is_snb_ep(p);
  case LIKWID_UNCORE_PROFILE_IMC_IVB:
    return intel_processor_is_ivb_ep(p);
  case LIKWID_UNCORE_PROFILE_IMC_HSW:
    return intel_processor_is_hsw_ep(p);
  case LIKWID_UNCORE_PROFILE_IMC_BDW:
    return intel_processor_is_bdw_ep(p);
  case LIKWID_UNCORE_PROFILE_IMC_SKX:
  case LIKWID_UNCORE_PROFILE_CHA_SKX:
    return intel_processor_is_skx_server(p);
  case LIKWID_UNCORE_PROFILE_IMC_ICX:
    return intel_processor_is_icx(p);
  case LIKWID_UNCORE_PROFILE_IMC_SPR:
    return intel_processor_is_spr(p);
  case LIKWID_UNCORE_PROFILE_CBO_SNB:
    return p == SANDYBRIDGE;
  case LIKWID_UNCORE_PROFILE_CBO_IVB:
    return p == IVYBRIDGE;
  case LIKWID_UNCORE_PROFILE_CBO_HSW:
    return p == HASWELL;
  case LIKWID_UNCORE_PROFILE_CBO_BDW:
    return p == BROADWELL;
  case LIKWID_UNCORE_PROFILE_QPI_SNB:
    return p == SANDYBRIDGE;
  case LIKWID_UNCORE_PROFILE_QPI_IVB:
    return p == IVYBRIDGE;
  case LIKWID_UNCORE_PROFILE_QPI_HSW:
    return p == HASWELL;
  case LIKWID_UNCORE_PROFILE_QPI_BDW:
    return p == BROADWELL;
  case LIKWID_UNCORE_PROFILE_HAU_SNB:
    return p == SANDYBRIDGE;
  case LIKWID_UNCORE_PROFILE_HAU_IVB:
    return p == IVYBRIDGE;
  case LIKWID_UNCORE_PROFILE_HAU_HSW:
    return p == HASWELL;
  case LIKWID_UNCORE_PROFILE_HAU_BDW:
    return p == BROADWELL;
  case LIKWID_UNCORE_PROFILE_R2PCI_SNB:
    return p == SANDYBRIDGE;
  case LIKWID_UNCORE_PROFILE_R2PCI_IVB:
    return p == IVYBRIDGE;
  case LIKWID_UNCORE_PROFILE_R2PCI_HSW:
    return p == HASWELL;
  case LIKWID_UNCORE_PROFILE_R2PCI_BDW:
    return p == BROADWELL;
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

static int map_mbox_hbm_mdev(const char *counter_name, char *dev_out,
                             size_t dev_len, const char **key_out)
{
  unsigned int idx = 0;
  char kind[8];
  char ch[4];

  if (counter_name == NULL || dev_out == NULL || key_out == NULL)
    return -1;

  if (sscanf(counter_name, "MBOX%uC%1s", &idx, ch) == 2) {
    snprintf(dev_out, dev_len, "mbox%u", idx);
    if (strcmp(ch, "0") == 0)
      *key_out = "dram_cas_reads";
    else
      *key_out = "dram_cas_writes";
    return 0;
  }
  if (sscanf(counter_name, "HBM%uC%1s", &idx, ch) == 2) {
    snprintf(dev_out, dev_len, "hbm%u", idx);
    if (strcmp(ch, "0") == 0)
      *key_out = "hbm_cas_reads";
    else
      *key_out = "hbm_cas_writes";
    return 0;
  }
  if (sscanf(counter_name, "MDEV%uC%1s", &idx, ch) == 2) {
    snprintf(dev_out, dev_len, "mdev%u", idx);
    if (strcmp(ch, "0") == 0)
      *key_out = "dram_cas_reads";
    else
      *key_out = "dram_cas_writes";
    return 0;
  }
  (void)kind;
  return -1;
}

int likwid_uncore_profile_map_counter(likwid_uncore_profile_t profile,
                                      const char *counter_name,
                                      char *dev_out, size_t dev_len,
                                      const char **key_out)
{
  (void)profile;
  return map_mbox_hbm_mdev(counter_name, dev_out, dev_len, key_out);
}
