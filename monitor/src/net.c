#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <linux/if.h>
#include "stats.h"
#include "collect.h"
#include "pscanf.h"
#include "trace.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"

#define KEYS \
  X(collisions, "E", ""), \
  X(multicast, "E", ""), \
  X(rx_bytes, "E,U=B", ""), \
  X(rx_compressed, "E", ""), \
  X(rx_crc_errors, "E", ""), \
  X(rx_dropped, "E", ""), \
  X(rx_errors, "E", ""), \
  X(rx_fifo_errors, "E", ""), \
  X(rx_frame_errors, "E", ""), \
  X(rx_length_errors, "E", ""), \
  X(rx_missed_errors, "E", ""), \
  X(rx_over_errors, "E", ""), \
  X(rx_packets, "E", ""), \
  X(tx_aborted_errors, "E", ""), \
  X(tx_bytes, "E,U=B", ""), \
  X(tx_carrier_errors, "E", ""), \
  X(tx_compressed, "E", ""), \
  X(tx_dropped, "E", ""), \
  X(tx_errors, "E", ""), \
  X(tx_fifo_errors, "E", ""), \
  X(tx_heartbeat_errors, "E", ""), \
  X(tx_packets, "E", ""), \
  X(tx_window_errors, "E", "")

/* Skip opendir/readdir + per-iface flags pscanf between rebuilds (hotplug: refreshed periodically). */
#define NET_IFACE_CACHE_REFRESH_INTERVAL 32u

static char **net_cached_devs;
static size_t net_n_cached;
static unsigned net_ticks_since_rebuild;

void net_stats_invalidate_iface_cache(void)
{
  size_t i;

  for (i = 0; i < net_n_cached; i++)
    free(net_cached_devs[i]);
  free(net_cached_devs);
  net_cached_devs = NULL;
  net_n_cached = 0;
  net_ticks_since_rebuild = 0;
}

static int net_iface_cache_append(const char *name)
{
  char **nlist;
  char *copy = strdup(name);

  if (copy == NULL)
    return -1;
  nlist = (char **)realloc(net_cached_devs, (net_n_cached + 1) * sizeof(*nlist));
  if (nlist == NULL) {
    free(copy);
    return -1;
  }
  net_cached_devs = nlist;
  net_cached_devs[net_n_cached++] = copy;
  return 0;
}

#define NET_FLAGS \
  X(IFF_UP), \
  X(IFF_BROADCAST), \
  X(IFF_DEBUG), \
  X(IFF_LOOPBACK), \
  X(IFF_POINTOPOINT), \
  X(IFF_NOTRAILERS), \
  X(IFF_RUNNING), \
  X(IFF_NOARP), \
  X(IFF_PROMISC), \
  X(IFF_ALLMULTI), \
  X(IFF_MASTER), \
  X(IFF_SLAVE), \
  X(IFF_MULTICAST), \
  X(IFF_VOLATILE), \
  X(IFF_PORTSEL), \
  X(IFF_AUTOMEDIA), \
  X(IFF_DYNAMIC)

static void net_iface_cache_each(const char *base, const char *name, void *ctx)
{
  unsigned int flags;
  char flags_path[80];

  (void)base;
  (void)ctx;
  snprintf(flags_path, sizeof(flags_path), "/sys/class/net/%s/flags", name);
  if (pscanf(flags_path, "%x", &flags) != 1)
    return;

#define X(F) ((flags & F) ? " " #F : "")
  TRACE("dev %s, flags %u%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n",
        name, flags, NET_FLAGS);
#undef X

  if ((flags & IFF_UP) && net_iface_cache_append(name) < 0)
    ERROR("cannot cache net iface `%s': %m\n", name);
}

static void net_iface_cache_rebuild(struct stats_type *type)
{
  (void)type;
  net_stats_invalidate_iface_cache();
  sys_iter_for_each("/sys/class/net", net_iface_cache_each, NULL);
}

static void net_collect_dev(struct stats_type *type, const char *dev)
{
  struct stats *stats = NULL;
  char path[80];

  stats = get_current_stats(type, dev);
  if (stats == NULL)
    return;

  snprintf(path, sizeof(path), "/sys/class/net/%s/statistics", dev);
  path_collect_key_value_dir(path, stats);
}

static void net_collect(struct stats_type *type)
{
  size_t i;

  net_ticks_since_rebuild++;
  if (net_n_cached == 0 || net_ticks_since_rebuild >= NET_IFACE_CACHE_REFRESH_INTERVAL) {
    net_ticks_since_rebuild = 0;
    net_iface_cache_rebuild(type);
  }

  for (i = 0; i < net_n_cached; i++)
    net_collect_dev(type, net_cached_devs[i]);
}

struct stats_type net_stats_type = {
  .st_name = "net",
  .st_collect = &net_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
