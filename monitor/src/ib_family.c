/* host_ib family - one sysfs walk per cycle for sysfs and MAD collectors. */
#include <errno.h>
#include <stdio.h>

#include "collect.h"
#include "stats.h"
#include "ib_common.h"
#include "ib_family.h"
#include "ib_mad.h"

/* Extended-counter host_ib keys: sysfs serves the packet counts when the port has their files
 * (ib_ext_collect_sysfs), else the MAD query serves all of them (port_xmit_data / port_rcv_data
 * are excluded: sysfs supplies them when MAD does not). */
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

/* Each extended packet key and the kernel counters/ file holding the same counter, cumulative
 * since boot (the PortCounters behind the MAD query may be cleared by fabric management). */
static const char *const ib_ext_sysfs_src[][2] = {
    {"port_xmit_pkts", "port_xmit_packets"},
    {"port_rcv_pkts", "port_rcv_packets"},
    {"port_unicast_xmit_pkts", "unicast_xmit_packets"},
    {"port_unicast_rcv_pkts", "unicast_rcv_packets"},
    {"port_multicast_xmit_pkts", "multicast_xmit_packets"},
    {"port_multicast_rcv_pkts", "multicast_rcv_packets"},
};
enum { IB_EXT_SYSFS_NR = sizeof(ib_ext_sysfs_src) / sizeof(ib_ext_sysfs_src[0]) };

/* Serve the extended packet keys from counters/ when the port has every source file: a file
 * that exists but does not read gives its key that read's code, and port_select /
 * counter_select have no source (NOT_PRESENT). Returns 0 at the first file whose read reports
 * absence (ENOENT, also for a path whose open failed before): the MAD query serves the port. */
static int ib_ext_collect_sysfs(struct stats *stats, const char *hca, int port)
{
  unsigned long long val[IB_EXT_SYSFS_NR];
  enum stats_sentinel code[IB_EXT_SYSFS_NR];
  char path[256];

  for (size_t i = 0; i < IB_EXT_SYSFS_NR; i++) {
    snprintf(path, sizeof(path), "%s/class/infiniband/%s/ports/%d/counters/%s", g_ib_sysfs_root,
             hca, port, ib_ext_sysfs_src[i][1]);
    switch (path_collect_single(path, &val[i])) {
    case 1:
      code[i] = STATS_SENTINEL_OK;
      break;
    case 0:
      code[i] = STATS_SENTINEL_READ_FAILED;
      break;
    default:
      code[i] = stats_sentinel_for_errno(errno);
    }
    if (code[i] == STATS_SENTINEL_NOT_PRESENT)
      return 0;
  }
  ib_family_mark_mad_keys(stats, 0, STATS_SENTINEL_NOT_PRESENT);
  for (size_t i = 0; i < IB_EXT_SYSFS_NR; i++) {
    if (code[i] == STATS_SENTINEL_OK)
      stats_set(stats, ib_ext_sysfs_src[i][0], val[i]);
    else
      stats_set_missing(stats, ib_ext_sysfs_src[i][0], code[i]);
  }
  return 1;
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
  int ext_sysfs = ib_ext_collect_sysfs(stats, hca, port);

#if defined(MONITOR_WITH_INFINIBAND)
  enum stats_sentinel code =
      ext_sysfs ? STATS_SENTINEL_OK : ib_mad_ext_collect_port(stats, hca, port);

  if (code != STATS_SENTINEL_OK)
    ib_family_mark_mad_keys(stats, 0, code);
  code = ib_mad_sw_collect_port(stats, hca, port);
  if (code != STATS_SENTINEL_OK)
    ib_family_mark_mad_keys(stats, 1, code);
#else
  if (!ext_sysfs)
    ib_family_mark_mad_keys(stats, 0, STATS_SENTINEL_NOT_BUILT);
  ib_family_mark_mad_keys(stats, 1, STATS_SENTINEL_NOT_BUILT);
#endif
}

/* A failed walk reached no port this cycle: every existing row gets the opendir errno code. */
void ib_family_collect(struct stats_type *type)
{
  if (type != NULL && type->st_enabled && ib_foreach_hca_port(ib_family_collect_port, type) < 0)
    stats_type_set_missing_all(type, stats_sentinel_for_errno(errno));
}

void ib_family_disable_all(void)
{
  struct stats_type *type = stats_type_get("host_ib");

  if (type != NULL)
    type->st_enabled = 0;
}
