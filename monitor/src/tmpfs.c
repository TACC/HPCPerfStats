/* host_tmpfs — /tmp tmpfs space and inode usage from /proc/mounts + statfs. */
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <mntent.h>
#include <sys/statfs.h>
#include "stats.h"
#include "trace.h"
#include "path_open_fail_once.h"

#define KEYS                                                                                       \
  X(bytes_used, "U=B", "bytes used"), X(bytes_avail, "U=B", "bytes available"),                    \
      X(files_used, "", "files used")

static void tmpfs_collect(struct stats_type *type)
{
  const char *me_path = "/proc/mounts";
  FILE *me_file = NULL;

  if (path_open_is_skipped(me_path))
    goto out;
  me_file = setmntent(me_path, "r");
  if (me_file == NULL) {
    path_open_record_failure_once(me_path);
    goto out;
  }

  struct mntent me;
  char me_buf[4096];
  while (getmntent_r(me_file, &me, me_buf, sizeof(me_buf)) != NULL) {
    struct stats *stats = NULL;
    struct statfs sfs;

    if (strcmp(me.mnt_type, "tmpfs") != 0)
      continue;
    if (strcmp(me.mnt_dir, "/tmp") != 0)
      continue;

    stats = get_current_stats(type, me.mnt_dir);
    if (stats == NULL)
      continue;

    if (statfs(me.mnt_dir, &sfs) < 0) {
      ERROR("cannot stat filesystem `%s': %m\n", me.mnt_dir);
      continue;
    }

    unsigned long long bytes_used = sfs.f_frsize * (sfs.f_blocks - sfs.f_bfree);
    unsigned long long bytes_avail = sfs.f_frsize * sfs.f_bavail;
    unsigned long long files_used = sfs.f_files - sfs.f_ffree;

    stats_set(stats, "bytes_used", bytes_used);
    stats_set(stats, "bytes_avail", bytes_avail);
    stats_set(stats, "files_used", files_used);
  }

out:
  if (me_file != NULL)
    endmntent(me_file);
}

struct stats_type tmpfs_stats_type = {
    .st_name = "host_tmpfs",
    .st_collect = &tmpfs_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
