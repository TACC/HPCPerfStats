#ifndef LUSTRE_MDC_H_
#define LUSTRE_MDC_H_

#include "stats.h"

#define KEYS                                                                                       \
  X(ldlm_cancel, "E", ""), X(mds_close, "E", ""), X(mds_getattr, "E", ""),                         \
      X(mds_getattr_lock, "E", ""), X(mds_getxattr, "E", ""), X(mds_readpage, "E", ""),            \
      X(mds_statfs, "E", ""), X(mds_sync, "E", ""), X(reqs, "E", ""), X(wait, "E,U=us", "")

/* Sums the lifetime counters of MDC `d_name` under `dir` into its mount row `stats` (the MDCs of
 * one mount share the row; see stats_accumulate). An unreadable file marks every key with its
 * errno code. */
void lustre_mdc_collect_obd(struct stats *stats, const char *dir, const char *d_name);

#endif
