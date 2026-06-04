#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "stats.h"
#include "collect.h"
#include "sys_iter.h"
#include "trace.h"

// # cat /sys/devices/system/node/node0/numastat
// numa_hit 24972369
// numa_miss 41182663
// numa_foreign 12112910
// interleave_hit 49948
// local_node 24910136
// other_node 41244896

#define KEYS \
  X(numa_hit, "E", ""), \
  X(numa_miss, "E", ""), \
  X(numa_foreign, "E", ""), \
  X(interleave_hit, "E", ""), \
  X(local_node, "E", ""), \
  X(other_node, "E", "")

static void numa_collect_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  struct stats *stats = NULL;
  const char *node;
  char path[80];

  if (strncmp(name, "node", 4) != 0)
    return;
  node = name + 4;

  stats = get_current_stats(type, node);
  if (stats == NULL)
    return;

  snprintf(path, sizeof(path), "%s/%s/numastat", base, name);
  path_collect_key_value(path, stats);
}

static void numa_collect(struct stats_type *type)
{
  sys_iter_for_each("/sys/devices/system/node", numa_collect_each, type);
}

struct stats_type numa_stats_type = {
  .st_name = "host_numa",
  .st_collect = &numa_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
