#ifndef _LIKWID_UNCORE_PROFILES_H_
#define _LIKWID_UNCORE_PROFILES_H_

#include "cpuid.h"

typedef enum {
  LIKWID_UNCORE_PROFILE_IMC_SKX = 0,
  LIKWID_UNCORE_PROFILE_IMC_ICX,
  LIKWID_UNCORE_PROFILE_IMC_SPR,
  LIKWID_UNCORE_PROFILE_IMC_EMR,
  LIKWID_UNCORE_PROFILE_IMC_GNR,
  LIKWID_UNCORE_PROFILE_IMC_SRF,
  LIKWID_UNCORE_PROFILE_CHA_SKX,
  LIKWID_UNCORE_PROFILE_DF_ROME,
  LIKWID_UNCORE_PROFILE_DF_MILAN,
  LIKWID_UNCORE_PROFILE_DF_GENOA,
  LIKWID_UNCORE_PROFILE_DF_TURIN,
  LIKWID_UNCORE_PROFILE_COUNT
} likwid_uncore_profile_t;

int likwid_uncore_profile_matches_processor(likwid_uncore_profile_t profile, processor_t p);
const char *likwid_uncore_profile_eventset(likwid_uncore_profile_t profile);
int likwid_uncore_profile_map_counter(likwid_uncore_profile_t profile, const char *counter_name,
                                      char *dev_out, size_t dev_len, const char **key_out);

typedef enum {
  LIKWID_SPR_IMC_EVT_DDR_HBM = 0,
  LIKWID_SPR_IMC_EVT_DDR_ONLY,
  LIKWID_SPR_IMC_EVT_HBM_ONLY,
} likwid_spr_imc_eventset_t;

const char *likwid_spr_imc_eventset_string(likwid_spr_imc_eventset_t variant);
const char *likwid_spr_imc_eventset_variant_name(likwid_spr_imc_eventset_t variant);
int likwid_spr_imc_eventset_try_order(int has_ddr, int has_hbm, likwid_spr_imc_eventset_t *out,
                                      int out_cap);
const char *likwid_spr_imc_hbm_channels_eventset(int n_channels);
int likwid_spr_imc_hbm_ladder_sizes(int *out, int out_cap);

#endif
