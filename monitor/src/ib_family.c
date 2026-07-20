/* host_ib family — one sysfs walk per cycle for sysfs and MAD collectors. */
#include <stdio.h>

#include "collect.h"
#include "stats.h"
#include "ib_common.h"
#include "ib_family.h"
#include "ib_mad.h"

static void ib_sysfs_collect_port(struct stats *stats, const char *hca, int port)
{
  char path[160];

  if (stats == NULL || hca == NULL)
    return;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/counters", hca, port);
  (void)path_collect_key_value_dir(path, stats);
  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/hw_counters", hca, port);
  (void)path_collect_key_value_dir(path, stats);
}

static void ib_family_collect_port(const char *hca, int port, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  char id[80];
  struct stats *stats;

  if (type == NULL || hca == NULL)
    return;

  snprintf(id, sizeof(id), "%s.%i", hca, port);
  stats = get_current_stats(type, id);
  if (stats == NULL)
    return;

  ib_sysfs_collect_port(stats, hca, port);

#if defined(MONITOR_WITH_INFINIBAND)
  if (ib_mad_ext_collect_cycle_ok())
    ib_mad_ext_collect_port(stats, hca, port);
  if (ib_mad_sw_collect_cycle_ok())
    ib_mad_sw_collect_port(stats, hca, port);
#endif
}

void ib_family_collect(struct stats_type *type)
{
  if (type == NULL || !type->st_enabled)
    return;
  ib_foreach_hca_port(ib_family_collect_port, type);
}

void ib_family_disable_all(void)
{
  struct stats_type *type = stats_type_get("host_ib");

  if (type != NULL)
    type->st_enabled = 0;
}
