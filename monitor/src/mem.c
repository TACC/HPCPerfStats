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
#include "fileio.h"
#include "trace.h"
#include "path_open_fail_once.h"

// i182-101# cat /sys/devices/system/node/node0/meminfo
//
// Node 0 MemTotal:      8220940 kB
// Node 0 MemFree:       4559756 kB
// Node 0 MemUsed:       3661184 kB
// Node 0 Active:        2220752 kB
// Node 0 Inactive:       859880 kB
// Node 0 HighTotal:           0 kB
// Node 0 HighFree:            0 kB
// Node 0 LowTotal:      8220940 kB
// Node 0 LowFree:       4559756 kB
// Node 0 Dirty:               4 kB
// Node 0 Writeback:           0 kB
// Node 0 FilePages:     2673328 kB
// Node 0 Mapped:          28296 kB
// Node 0 AnonPages:      406964 kB
// Node 0 PageTables:       2596 kB
// Node 0 NFS_Unstable:        0 kB
// Node 0 Bounce:              0 kB
// Node 0 Slab:           294400 kB
// Node 0 HugePages_Total:     0
// Node 0 HugePages_Free:      0

/* On 2.6.18-194.32.1 files in /dev/shm show up as FilePages in
   nodeN/meminfo and as Cached in /proc/meminfo. */

/* Dirty, Writeback, AnonPages, Mapped, Slab, PageTables,
   NFS_Unstable, Bounce. */

#define KEYS \
  X(MemTotal, "U=KB", ""), \
  X(MemFree, "U=KB", ""), \
  X(MemUsed, "U=KB", ""), \
  X(Active, "U=KB", ""), \
  X(Inactive, "U=KB", ""), \
  X(Dirty, "U=KB", ""), \
  X(Writeback, "U=KB", ""), \
  X(FilePages, "U=KB", ""), \
  X(Mapped, "U=KB", ""), \
  X(AnonPages, "U=KB", ""), \
  X(PageTables, "U=KB", ""), \
  X(NFS_Unstable, "U=KB", ""), \
  X(Bounce, "U=KB", ""), \
  X(Slab, "U=KB", ""), \
  X(AnonHugePages, "U=KB", ""), \
  X(HugePages_Total, "", ""), \
  X(HugePages_Free, "", "")

static void mem_collect_node(struct stats *stats, const char *node)
{
  char path[80];
  FILE *file = NULL;
  char file_buf[4096];
  char *line = NULL;
  size_t line_size = 0;

  snprintf(path, sizeof(path), "/sys/devices/system/node/node%s/meminfo", node);
  file = path_file_fopen_read(path);
  if (file == NULL)
    goto out;
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  while (getline(&line, &line_size, file) >= 0) {
    char key[81];
    unsigned long long val = 0;

    key[0] = 0;
    if (sscanf(line, "Node %*d %80[^:]: %llu %*s", key, &val) < 2)
      continue;

    if (key[0] == 0)
      continue;

    stats_set(stats, key, val);
  }

 out:
  free(line);
  if (file != NULL)
    fclose(file);
}

static void mem_collect(struct stats_type *type)
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

    if (strncmp(ent->d_name, "node", 4) != 0)
      continue;

    node = ent->d_name + 4;
    stats = get_current_stats(type, node);
    if (stats == NULL)
      continue;

    mem_collect_node(stats, node);
  }

 out:
  if (dir != NULL)
    closedir(dir);
}

struct stats_type mem_stats_type = {
  .st_name = "mem",
  .st_collect = &mem_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
