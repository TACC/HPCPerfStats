/* host_lnet — Lustre LNET router/client counters from /proc/sys/lnet/stats. */
#include <stddef.h>
#include "stats.h"
#include "collect.h"
#include "trace.h"

/* Field order matches lustre lnet/router_proc.c; see kernel comment in tree. */

#define KEYS                                                                                       \
  X(msgs_alloc, "E", ""), X(msgs_alloc_max, "E", ""), X(errors, "E", ""), X(tx_msgs, "E", ""),     \
      X(rx_msgs, "E", ""), X(route_msgs, "E", ""), X(rx_msgs_dropped, "E", ""),                    \
      X(tx_bytes, "E,U=B", ""), X(rx_bytes, "E,U=B", ""), X(route_bytes, "E,U=B", ""),             \
      X(rx_bytes_dropped, "E,U=B", "")

static void lnet_collect(struct stats_type *type)
{
  struct stats *stats;

  if (type == NULL)
    return;
  stats = get_current_stats(type, NULL);
  if (stats == NULL)
    return;

#define X(k, r...) #k
  path_collect_key_list("/proc/sys/lnet/stats", stats, KEYS, NULL);
#undef X
}

struct stats_type lnet_stats_type = {
    .st_name = "host_lnet",
    .st_collect = &lnet_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
