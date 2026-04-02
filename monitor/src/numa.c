#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include "stats.h"
#include "collect.h"
#include "trace.h"
#include "path_open_fail_once.h"

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

static void numa_collect(struct stats_type *type)
{
  const char *dir_path = "/sys/devices/system/node";
  DIR *dir = NULL;

  dir = path_opendir_or_record_fail(dir_path);
  if (dir == NULL)
    goto out;

  struct dirent *ent;
  while ((ent = readdir(dir)) != NULL) {
    struct stats *stats = NULL;
    const char *node;
    char path[80];

    if (strncmp(ent->d_name, "node", 4) != 0)
      continue;

    node = ent->d_name + 4;
    stats = get_current_stats(type, node);
    if (stats == NULL)
      continue;

    snprintf(path, sizeof(path), "%s/node%s/numastat", dir_path, node);
    path_collect_key_value(path, stats);
  }

 out:
  if (dir != NULL)
    closedir(dir);
}

struct stats_type numa_stats_type = {
  .st_name = "numa",
  .st_collect = &numa_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
