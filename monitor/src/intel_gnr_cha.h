#ifndef _INTEL_GNR_CHA_H_
#define _INTEL_GNR_CHA_H_

/* GNR: REQUESTS_READS + LLC_VICTIMS_LOCAL_M only. */
#define INTEL_GNR_CHA_KEYS                                                                         \
  X(sf_evictions_mes, "E,W=48", ""), X(llc_lookup_data_read_local, "E,W=48", "")

#endif
