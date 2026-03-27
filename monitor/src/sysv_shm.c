#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"

// From ipc/shm.c
// # cat /proc/sysvipc/shm
// key      shmid perms       size  cpid  lpid nattch   uid   gid  cuid  cgid      atime      dtime      ctime
//   0     131072   666    1048576  2720  2720      1     0     0     0     0 1304962654          0 1304962654

// "%10d %10d  %4o %10u %5u %5u  %5d %5u %5u %5u %5u %10lu %10lu %10lu\n"
// return seq_printf(s, format,
//                   shp->shm_perm.key,
//                   shp->id,
//                   shp->shm_perm.mode,
//                   shp->shm_segsz,
//                   shp->shm_cprid,
//                   shp->shm_lprid,
//                   is_file_hugepages(shp->shm_file) ? (file_count(shp->shm_file) - 1) : shp->shm_nattch,
//                   shp->shm_perm.uid,
//                   shp->shm_perm.gid,
//                   shp->shm_perm.cuid,
//                   shp->shm_perm.cgid,
//                   shp->shm_atim,
//                   shp->shm_dtim,
//                   shp->shm_ctim);

#define KEYS \
  X(mem_used, "U=B", "System V shared memory used"), \
  X(segs_used, "", "number of System V shared segments used")

static void sysv_shm_collect(struct stats_type *type)
{
  struct stats *stats = NULL;
  const char *path = "/proc/sysvipc/shm";
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  stats = get_current_stats(type, NULL);
  if (stats == NULL)
    goto out;

  file = file_fopen_read(path);
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  /* Skip header. */
  if (getline(&line_buf, &line_buf_size, file) < 0)
    goto out;

  unsigned long long mem_used = 0, segs_used = 0;

  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    unsigned long long seg_size = 0;
    if (sscanf(line_buf, "%*d %*d %*o %llu", &seg_size) < 1)
      continue;

    mem_used += seg_size;
    segs_used++;
  }

  stats_set(stats, "mem_used", mem_used);
  stats_set(stats, "segs_used", segs_used);

 out:
  free(line_buf);
  if (file != NULL)
    fclose(file);
}

struct stats_type sysv_shm_stats_type = {
  .st_name = "sysv_shm",
  .st_collect = &sysv_shm_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
