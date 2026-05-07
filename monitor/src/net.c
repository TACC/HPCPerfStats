#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include <time.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <linux/if.h>
#include "stats.h"
#include "collect.h"
#include "pscanf.h"
#include "trace.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"
#include "monitor_log.h"

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
static time_t net_rebuild_skip_until;

static int net_env_int_or_default(const char *name, int fallback)
{
  const char *v = getenv(name);
  char *end = NULL;
  long parsed;

  if (v == NULL || *v == '\0')
    return fallback;
  parsed = strtol(v, &end, 10);
  if (end == v || *end != '\0' || parsed <= 0)
    return fallback;
  if (parsed > 86400L)
    return 86400;
  return (int)parsed;
}

static long long net_monotonic_us(void)
{
  struct timespec ts;

  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return -1;
  return (long long) ts.tv_sec * 1000000LL + (long long) ts.tv_nsec / 1000LL;
}

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
  long long started_us = net_monotonic_us();
  long long elapsed_us = -1;
  int slow_warn_ms = net_env_int_or_default("HPCPERFSTATS_NET_REBUILD_WARN_MS", 50);
  int skip_sec = net_env_int_or_default("HPCPERFSTATS_NET_REBUILD_SKIP_SEC", 120);

  (void)type;
  net_stats_invalidate_iface_cache();
  sys_iter_for_each("/sys/class/net", net_iface_cache_each, NULL);
  if (started_us > 0) {
    elapsed_us = net_monotonic_us() - started_us;
    if (elapsed_us > (long long)slow_warn_ms * 1000LL) {
      monitor_log_warn("net cache rebuild slow: elapsed_us=%lld ifaces=%zu; skip rebuild %ds\n",
		       elapsed_us, net_n_cached, skip_sec);
      net_rebuild_skip_until = time(NULL) + skip_sec;
    }
  }
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
    time_t now = time(NULL);
    if (net_n_cached != 0 && net_rebuild_skip_until > 0 && now > 0 && now < net_rebuild_skip_until)
      return;
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
