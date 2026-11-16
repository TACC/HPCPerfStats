/* host_vfs — VFS dentry/file/inode usage from procfs sys/fs. */
#include <stddef.h>
#include "stats.h"
#include "collect.h"

#define KEYS                                                                                       \
  X(dentry_use, "", "number of directory entries in use"),                                         \
      X(file_use, "", "number of file handles in use"),                                            \
      X(inode_use, "", "number of inodes in use")

static void vfs_collect(struct stats_type *type)
{
  struct stats *stats = NULL;
#define X(k, r...) k = 0
  unsigned long long KEYS;
#undef X

  stats = get_current_stats(type, NULL);
  if (stats == NULL)
    return;

  /* dentry-state: nr_dentry nr_unused age_limit want_pages + 2 dummies. */
  unsigned long long dentry_alloc = 0, dentry_free = 0;
  if (path_collect_list("/proc/sys/fs/dentry-state", &dentry_alloc, &dentry_free, NULL) == 2)
    dentry_use = dentry_alloc - dentry_free;

  /* file-nr: allocated, free (always 0 since Linux 2.6, so allocated == used), max. */
  path_collect_single("/proc/sys/fs/file-nr", &file_use);

  /* inode-state: nr_inodes nr_free_inodes preshrink + 4 dummies. */
  unsigned long long inode_alloc = 0, inode_free = 0;
  if (path_collect_list("/proc/sys/fs/inode-state", &inode_alloc, &inode_free, NULL) == 2)
    inode_use = inode_alloc - inode_free;

#define X(k, r...) stats_set(stats, #k, k)
  KEYS;
#undef X
}

struct stats_type vfs_stats_type = {
    .st_name = "host_vfs",
    .st_collect = &vfs_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
