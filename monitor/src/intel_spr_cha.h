#ifndef _INTEL_SPR_CHA_H_
#define _INTEL_SPR_CHA_H_

/* SPR/EMR: no LLC_LOOKUP_WRITE; bypass on C2. */
#define INTEL_SPR_CHA_KEYS                                                                         \
  X(sf_evictions_mes, "E,W=48", ""), X(llc_lookup_data_read_local, "E,W=48", ""),                  \
      X(bypass_cha_imc_all, "E,W=48", "")

#endif
