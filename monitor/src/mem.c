/* host_mem — NUMA node memory counters from /sys/.../nodeN/meminfo. */
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "stats.h"
#include "collect.h"
#include "procfile_parse.h"
#include "sys_iter.h"
#include "trace.h"
#include "host_key_alias.h"

// i182-101# cat /sys/devices/system/node/node0/meminfo
//
// Node 0 MemTotal:      8220940 kB
// Node 0 MemFree:       4559756 kB
// Node 0 MemUsed:       3661184 kB
// ...

/* On 2.6.18-194.32.1 files in /dev/shm show up as FilePages in
   nodeN/meminfo and as Cached in /proc/meminfo. */

#define KEYS \
  X(mem_total, "U=KB", ""), \
  X(mem_free, "U=KB", ""), \
  X(mem_used, "U=KB", ""), \
  X(active, "U=KB", ""), \
  X(inactive, "U=KB", ""), \
  X(dirty, "U=KB", ""), \
  X(writeback, "U=KB", ""), \
  X(file_pages, "U=KB", ""), \
  X(mapped, "U=KB", ""), \
  X(anon_pages, "U=KB", ""), \
  X(page_tables, "U=KB", ""), \
  X(nfs_unstable, "U=KB", ""), \
  X(bounce, "U=KB", ""), \
  X(slab, "U=KB", ""), \
  X(anon_huge_pages, "U=KB", ""), \
  X(huge_pages_total, "", ""), \
  X(huge_pages_free, "", "")

static int mem_meminfo_line_cb(char *line, void *ctx)
{
  struct stats *stats = (struct stats *)ctx;
  char key[81];
  unsigned long long val = 0;

  key[0] = 0;
  if (sscanf(line, "Node %*d %80[^:]: %llu %*s", key, &val) < 2)
    return 0;
  if (key[0] == 0)
    return 0;
  host_key_alias_emit(stats, key, val);
  return 0;
}

static void mem_collect_node(struct stats *stats, const char *node)
{
  char path[80];

  snprintf(path, sizeof(path), "/sys/devices/system/node/node%s/meminfo", node);
  procfile_for_each_line(path, mem_meminfo_line_cb, stats);
}

static void mem_collect_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  struct stats *stats;

  (void)base;
  if (strncmp(name, "node", 4) != 0)
    return;

  stats = get_current_stats(type, name + 4);
  if (stats == NULL)
    return;

  mem_collect_node(stats, name + 4);
}

static void mem_collect(struct stats_type *type)
{
  sys_iter_for_each("/sys/devices/system/node", mem_collect_each, type);
}

struct stats_type mem_stats_type = {
  .st_name = "host_mem",
  .st_collect = &mem_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
