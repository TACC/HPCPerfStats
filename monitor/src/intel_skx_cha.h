#ifndef _INTEL_SKX_CHA_H_
#define _INTEL_SKX_CHA_H_

/* SKX/CLX and ICX CHA schema (lookup + victims + write + bypass). */
#define INTEL_SKX_CHA_KEYS                                                                         \
  X(sf_evictions_mes, "E,W=48", ""), X(llc_lookup_data_read_local, "E,W=48", ""),                  \
      X(bypass_cha_imc_all, "E,W=48", ""), X(llc_lookup_write, "E,W=48", "")

#endif
