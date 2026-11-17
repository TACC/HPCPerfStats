#ifndef _IB_FAMILY_H_
#define _IB_FAMILY_H_

#include "stats_sentinel.h"

struct stats;
struct stats_type;

void ib_family_collect(struct stats_type *type);
/* stats_set_missing(code) on the MAD-only host_ib keys: extended port counters (sw == 0) or
 * switch-port counters (sw != 0). */
void ib_family_mark_mad_keys(struct stats *stats, int sw, enum stats_sentinel code);
void ib_family_disable_all(void);
/* One host_ib port (`ctx` is the host_ib stats type): sysfs counters, then MAD when built. */
void ib_family_collect_port(const char *hca, int port, void *ctx);
/* Test hook: override the sysfs root (default "/sys"). Pass NULL to restore. */
void ib_family_test_set_sysfs_root(const char *root);

#endif
