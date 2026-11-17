/* host_vfs — VFS dentry/file/inode usage from procfs sys/fs. */
#include <stddef.h>
#include "stats.h"
#include "collect.h"

#define KEYS                                                                                       \
  X(dentry_use, "", "number of directory entries in use"),                                         \
      X(file_use, "", "number of file handles in use"),                                            \
      X(inode_use, "", "number of inodes in use")

/* Set `key` to alloc - free from the first two numbers of `path`, else READ_FAILED. */
static void vfs_collect_in_use(struct stats *stats, const char *path, const char *key)
{
  unsigned long long alloc = 0, free_n = 0;

  if (path_collect_list(path, &alloc, &free_n, NULL) == 2)
    stats_set(stats, key, alloc - free_n);
  else
    stats_set_missing(stats, key, STATS_SENTINEL_READ_FAILED);
}

static void vfs_collect(struct stats_type *type)
{
  struct stats *stats = get_current_stats(type, NULL);
  unsigned long long file_use = 0;

  if (stats == NULL)
    return;

  /* dentry-state: nr_dentry nr_unused age_limit want_pages + 2 dummies. */
  vfs_collect_in_use(stats, "/proc/sys/fs/dentry-state", "dentry_use");

  /* file-nr: allocated, free (always 0 since Linux 2.6, so allocated == used), max. */
  if (path_collect_single("/proc/sys/fs/file-nr", &file_use) == 1)
    stats_set(stats, "file_use", file_use);
  else
    stats_set_missing(stats, "file_use", STATS_SENTINEL_READ_FAILED);

  /* inode-state: nr_inodes nr_free_inodes preshrink + 4 dummies. */
  vfs_collect_in_use(stats, "/proc/sys/fs/inode-state", "inode_use");
}

struct stats_type vfs_stats_type = {
    .st_name = "host_vfs",
    .st_collect = &vfs_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
