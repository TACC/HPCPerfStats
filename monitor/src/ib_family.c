/* host_ib family - one sysfs walk per cycle for sysfs and MAD collectors. */
#include <stdio.h>

#include "collect.h"
#include "stats.h"
#include "ib_common.h"
#include "ib_family.h"
#include "ib_mad.h"

/* MAD-only host_ib keys: sysfs has no file for them (port_xmit_data / port_rcv_data are excluded:
 * sysfs supplies them when MAD does not). */
static const char *const ib_mad_ext_keys[] = {
    "port_select",
    "counter_select",
    "port_xmit_pkts",
    "port_rcv_pkts",
    "port_unicast_xmit_pkts",
    "port_unicast_rcv_pkts",
    "port_multicast_xmit_pkts",
    "port_multicast_rcv_pkts",
};
static const char *const ib_mad_sw_keys[] = {
    "sw_rx_bytes",
    "sw_rx_packets",
    "sw_tx_bytes",
    "sw_tx_packets",
};

void ib_family_mark_mad_keys(struct stats *stats, int sw, enum stats_sentinel code)
{
  const char *const *keys = sw ? ib_mad_sw_keys : ib_mad_ext_keys;
  size_t n = sw ? sizeof(ib_mad_sw_keys) / sizeof(ib_mad_sw_keys[0])
                : sizeof(ib_mad_ext_keys) / sizeof(ib_mad_ext_keys[0]);

  for (size_t i = 0; i < n; i++)
    stats_set_missing(stats, keys[i], code);
}

static const char *g_ib_sysfs_root = "/sys";

void ib_family_test_set_sysfs_root(const char *root)
{
  g_ib_sysfs_root = (root != NULL && root[0] != '\0') ? root : "/sys";
}

/* Counter files are the keys: a missing file is NOT_PRESENT, an unreadable directory or file
 * gets its errno code (collect.c). hw_counters may add keys counters lacks. */
static void ib_sysfs_collect_port(struct stats *stats, const char *hca, int port)
{
  char path[256];

  if (stats == NULL || hca == NULL)
    return;

  snprintf(path, sizeof(path), "%s/class/infiniband/%s/ports/%d/counters", g_ib_sysfs_root, hca,
           port);
  (void)path_collect_key_value_dir(path, stats);
  snprintf(path, sizeof(path), "%s/class/infiniband/%s/ports/%d/hw_counters", g_ib_sysfs_root, hca,
           port);
  (void)path_collect_key_value_dir(path, stats);
}

void ib_family_collect_port(const char *hca, int port, void *ctx)
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
  if (!ib_mad_ext_collect_cycle_ok())
    ib_family_mark_mad_keys(stats, 0, STATS_SENTINEL_BACKOFF);
  else if (ib_mad_ext_collect_port(stats, hca, port) < 0)
    ib_family_mark_mad_keys(stats, 0, STATS_SENTINEL_BACKEND_ERROR);
  if (!ib_mad_sw_collect_cycle_ok())
    ib_family_mark_mad_keys(stats, 1, STATS_SENTINEL_BACKOFF);
  else if (ib_mad_sw_collect_port(stats, hca, port) < 0)
    ib_family_mark_mad_keys(stats, 1, STATS_SENTINEL_BACKEND_ERROR);
#else
  ib_family_mark_mad_keys(stats, 0, STATS_SENTINEL_NOT_BUILT);
  ib_family_mark_mad_keys(stats, 1, STATS_SENTINEL_NOT_BUILT);
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
