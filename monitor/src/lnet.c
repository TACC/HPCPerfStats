#include <stddef.h>
#include "stats.h"
#include "collect.h"
#include "trace.h"

// # cat /proc/sys/lnet/stats
// # cat /sys/kernel/debug/lnet/stats -> Lustre Client > 2.6
// 0 1172 0 195805494 204125982 0 16957 216828482753 708781379083 0 3268048
//
// See lustre-1.8.5/lnet/lnet/router_proc.c

/* Keys match /proc/sys/lnet/stats field order (see comment above). */
#define KEYS \
  X(msgs_alloc, "E", ""), \
  X(msgs_alloc_max, "E", ""), \
  X(errors, "E", ""), \
  X(tx_msgs, "E", ""), \
  X(rx_msgs, "E", ""), \
  X(route_msgs, "E", ""), \
  X(rx_msgs_dropped, "E", ""), \
  X(tx_bytes, "E,U=B", ""), \
  X(rx_bytes, "E,U=B", ""), \
  X(route_bytes, "E,U=B", ""), \
  X(rx_bytes_dropped, "E,U=B", "")

static void lnet_collect(struct stats_type *type)
{
  struct stats *stats = get_current_stats(type, NULL);

  if (stats == NULL)
    return;

#define X(k,r...) #k
  path_collect_key_list("/proc/sys/lnet/stats", stats, KEYS, NULL);
#undef X
}

struct stats_type lnet_stats_type = {
  .st_name = "lnet",
  .st_collect = &lnet_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
