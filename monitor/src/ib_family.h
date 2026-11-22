#ifndef _IB_FAMILY_H_
#define _IB_FAMILY_H_

#include "stats_sentinel.h"

struct stats;
struct stats_type;

void ib_family_collect(struct stats_type *type);
/* stats_set_missing(code) on the switch-port counter keys (sw_*). */
void ib_family_mark_mad_keys(struct stats *stats, enum stats_sentinel code);
void ib_family_disable_all(void);
/* One host_ib port (`ctx` is the host_ib stats type): sysfs counters/ and hw_counters/, then
 * the switch-port MAD query; the sw_* keys are NOT_BUILT without MAD. */
void ib_family_collect_port(const char *hca, int port, void *ctx);
/* Test hook: override the sysfs root (default "/sys"). Pass NULL to restore. */
void ib_family_test_set_sysfs_root(const char *root);

#endif
