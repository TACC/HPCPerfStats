#include <errno.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#include "stats.h"
#include "trace.h"

// # cat /proc/sys/lnet/stats
// # cat /sys/kernel/debug/lnet/stats -> Lustre Client > 2.6
// 0 1172 0 195805494 204125982 0 16957 216828482753 708781379083 0 3268048
//
// See lustre-1.8.5/lnet/lnet/router_proc.c
// Values are from the_lnet.ln_counters {
//   msgs_alloc, // Number of currently active messages.
//   msgs_max, // Highwater of msgs_alloc.
//   errors, // Unused.
//   send_count, // Messages sent.
//   recv_count, // Messages dropped.
//   route_count, // Only used on routers?
//   drop_count, // Messages dropped, recv size only?
//   send_length, // Bytes sent.
//   recv_length, // Bytes received.
//   route_length, // Bytes of routed messages.  Routers only?
//   drop_length, // Bytes dropped, recv size only?
// }

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
  const char *path = "/proc/sys/lnet/stats";

#if !defined(__linux__)
  (void)type;
  return;
#else
  static const char *const lnet_keys[] = {
    "msgs_alloc", "msgs_alloc_max", "errors", "tx_msgs",
    "rx_msgs", "route_msgs", "rx_msgs_dropped", "tx_bytes",
    "rx_bytes", "route_bytes", "rx_bytes_dropped",
  };
  FILE *f;

  if (access(path, R_OK) != 0) {
    if (errno != ENOENT)
      TRACE("lnet: `%s' not readable: %m\n", path);
    return;
  }

  f = fopen(path, "r");
  if (f == NULL) {
    TRACE("lnet: fopen `%s': %m\n", path);
    return;
  }

  struct stats *stats = get_current_stats(type, NULL);
  if (stats == NULL) {
    fclose(f);
    return;
  }

  for (size_t i = 0; i < sizeof(lnet_keys) / sizeof(lnet_keys[0]); i++) {
    unsigned long long v;
    if (fscanf(f, "%llu", &v) != 1) {
      TRACE("lnet: short read from `%s' (key `%s')\n", path, lnet_keys[i]);
      fclose(f);
      return;
    }
    stats_set(stats, lnet_keys[i], v);
  }
  fclose(f);
#endif
}

struct stats_type lnet_stats_type = {
  .st_name = "lnet",
  .st_collect = &lnet_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
