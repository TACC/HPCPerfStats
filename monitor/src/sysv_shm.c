/* host_sysv_shm — System V shared memory usage from /proc/sysvipc/shm. */
#include <stdio.h>
#include "stats.h"
#include "procfile_parse.h"
#include "trace.h"

// From ipc/shm.c
// # cat /proc/sysvipc/shm
// key      shmid perms       size  cpid  lpid nattch   uid   gid  cuid  cgid      atime      dtime      ctime
//   0     131072   666    1048576  2720  2720      1     0     0     0     0 1304962654          0 1304962654
//
// "%10d %10d  %4o %10u %5u %5u  %5d %5u %5u %5u %5u %10lu %10lu %10lu\n"

#define KEYS \
  X(mem_used, "U=B", "System V shared memory used"), \
  X(segs_used, "", "number of System V shared segments used")

struct sysv_shm_acc {
  unsigned long long mem_used;
  unsigned long long segs_used;
};

static int sysv_shm_line_cb(char *line, void *ctx)
{
  struct sysv_shm_acc *acc = (struct sysv_shm_acc *)ctx;
  unsigned long long seg_size = 0;

  if (sscanf(line, "%*d %*d %*o %llu", &seg_size) < 1)
    return 0;
  acc->mem_used += seg_size;
  acc->segs_used++;
  return 0;
}

static void sysv_shm_collect(struct stats_type *type)
{
  struct stats *stats = get_current_stats(type, NULL);
  struct sysv_shm_acc acc = { 0, 0 };

  if (stats == NULL)
    return;

  /* Skip the one-line header. */
  if (procfile_for_each_line_skip("/proc/sysvipc/shm", 1,
                                  sysv_shm_line_cb, &acc) < 0)
    return;

  stats_set(stats, "mem_used", acc.mem_used);
  stats_set(stats, "segs_used", acc.segs_used);
}

struct stats_type sysv_shm_stats_type = {
  .st_name = "host_sysv_shm",
  .st_collect = &sysv_shm_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
