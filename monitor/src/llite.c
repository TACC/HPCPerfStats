#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "path_open_fail_once.h"
#include "procfile_parse.h"
#include "sys_iter.h"

#define LLITE_DIR_PATH "/proc/fs/lustre/llite"

/* Based on llite_opcode_table in lustre-1.8.5/lustre/llite/lproc_llite.c.

   In read_bytes, count is based on argument to read(), not return value.
   direct_{read,write} are not counted in {read,write}_bytes.
   direct_{read,write} are tallied in bytes not pages.
   brw_read is tallied in bytes not pages, and is only used in
   ll_prepare_write() when the write is not a complete page.
   brw_write is not used.
   lockless_{read,write}_bytes values seem to be same as direct_{read,write}.
   The writeback_* stats are unused.
   lockless_truncates are a subset of truncates.

   If you find out what 'regs' are then please let me know.
*/

#define KEYS \
  X(read, "E", ""), \
  X(write, "E", ""), \
  X(read_bytes, "E,U=B", ""), \
  X(write_bytes, "E,U=B", ""), \
  X(direct_read, "E,U=B", ""), \
  X(direct_write, "E,U=B", ""), \
  X(osc_read, "E,U=B", ""), \
  X(osc_write, "E,U=B", ""), \
  X(dirty_pages_hits, "E", ""), \
  X(dirty_pages_misses, "E", ""), \
  X(ioctl, "E", ""), \
  X(open, "E", ""), \
  X(close, "E", ""), \
  X(mmap, "E", ""), \
  X(seek, "E", ""), \
  X(fsync, "E", ""), \
  X(setattr, "E", ""), \
  X(truncate, "E", ""), \
  X(flock, "E", ""), \
  X(getattr, "E", ""), \
  X(statfs, "E", ""), \
  X(alloc_inode, "E", ""), \
  X(setxattr, "E", ""), \
  X(getxattr, "E", ""), \
  X(listxattr, "E", ""), \
  X(removexattr, "E", ""), \
  X(inode_permission, "E", ""), \
  X(readdir, "E", ""), \
  X(create, "E", ""), \
  X(lookup, "E", ""), \
  X(link, "E", ""), \
  X(unlink, "E", ""), \
  X(symlink, "E", ""), \
  X(mkdir, "E", ""), \
  X(rmdir, "E", ""), \
  X(mknod, "E", ""), \
  X(rename, "E", "")

static int llite_stats_line_cb(char *line, void *ctx)
{
  struct stats *stats = (struct stats *)ctx;
  char *rest = line;
  char *key = wsep(&rest);
  unsigned long long count = 0, sum = 0;
  int n;

  if (key == NULL || rest == NULL)
    return 0;

  n = sscanf(rest, "%llu samples %*s %*u %*u %llu", &count, &sum);
  if (n == 1)
    stats_set(stats, key, count);
  else if (n == 2)
    stats_set(stats, key, sum);
  return 0;
}

static void llite_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;

  if (asprintf(&path, "%s/%s/stats", LLITE_DIR_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    return;
  }
  procfile_for_each_line(path, llite_stats_line_cb, stats);
  free(path);
}

static void llite_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  const char *mnt = lustre_obd_to_mnt(name);
  struct stats *stats;

  (void)base;
  /* lustre_obd_to_mnt returns NULL for non-OBD entries (e.g. files like
   * "blocked_locks" if any), which serves as our type filter. */
  if (mnt == NULL)
    return;

  TRACE("d_name `%s', mnt `%s'\n", name, mnt);

  stats = get_current_stats(type, mnt);
  if (stats == NULL)
    return;

  llite_collect_fs(stats, name);
}

static void llite_collect(struct stats_type *type)
{
  sys_iter_for_each(LLITE_DIR_PATH, llite_each, type);
}

struct stats_type llite_stats_type = {
  .st_name = "lustre_llite",
  .st_collect = &llite_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
