#ifndef _IB_FAMILY_H_
#define _IB_FAMILY_H_

#include "stats_sentinel.h"

struct stats;
struct stats_type;

void ib_family_collect(struct stats_type *type);
/* stats_set_missing(code) on the extended port counter keys (sw == 0) or the switch-port counter
 * keys (sw != 0). */
void ib_family_mark_mad_keys(struct stats *stats, int sw, enum stats_sentinel code);
void ib_family_disable_all(void);
/* One host_ib port (`ctx` is the host_ib stats type): sysfs counters, the extended packet keys
 * from counters/ when the port has every source file (else the MAD extended query), then the
 * switch-port MAD query; MAD keys are NOT_BUILT without MAD. */
void ib_family_collect_port(const char *hca, int port, void *ctx);
/* Test hook: override the sysfs root (default "/sys"). Pass NULL to restore. */
void ib_family_test_set_sysfs_root(const char *root);

#endif
