#include <stddef.h>
#include <string.h>
#include "stats.h"
#include "collect.h"
#include "trace.h"
#include "sys_iter.h"

/* Need to account for units.  According to block/stat.txt, in
   /sys/block/DEV/stat sector means 512B (as opposed to real sector
   size of device). */
/* All X_ticks members and time_in_queue are in ms. */

#define KEYS \
  X(rd_ios,        "e",        "read requests processed"), \
  X(rd_merges,     "e",        "read requests merged with in-queue requests"), \
  X(rd_sectors,    "E,U=512B", "sectors read"), \
  X(rd_ticks,      "E,U=ms",   "wait time for read requests"), \
  X(wr_ios,        "e",        "write requests processed"), \
  X(wr_merges,     "e",        "write requests merged with in-queue requests"), \
  X(wr_sectors,    "E,U=512B", "sectors written"), \
  X(wr_ticks,      "E,U=ms",   "wait time for write requests"), \
  X(in_flight,     "",         "requests in flight"), \
  X(io_ticks,      "E,U=ms",   "time active"), \
  X(time_in_queue, "E,U=ms",   "wait time for all requests")

static void block_collect_dev(struct stats_type *type, const char *dev)
{
  struct stats *stats = get_current_stats(type, dev);
  if (stats == NULL)
    return;

  char path[80];
  snprintf(path, sizeof(path), "/sys/block/%s/stat", dev);

#define X(k,r...) #k
  path_collect_key_list(path, stats, KEYS, NULL);
#undef X
}

static void block_collect_each(const char *base, const char *name, void *ctx)
{
  (void)base;
  if (strncmp(name, "ram", 3) == 0)
    return;
  if (strncmp(name, "loop", 4) == 0)
    return;
  block_collect_dev((struct stats_type *)ctx, name);
}

static void block_collect(struct stats_type *type)
{
  sys_iter_for_each("/sys/block", block_collect_each, type);
}

struct stats_type block_stats_type = {
  .st_name = "host_block",
  .st_collect = &block_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
