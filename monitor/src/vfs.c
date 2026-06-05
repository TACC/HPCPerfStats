/* host_vfs — VFS dentry/file/inode usage from procfs sys/fs. */
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "stats.h"
#include "collect.h"
#include "trace.h"

#define KEYS \
  X(dentry_use, "", "number of directory entries in use"), \
  X(file_use, "", "number of file handles in use"), \
  X(inode_use, "", "number of inodes in use")

static void vfs_collect(struct stats_type *type)
{
  struct stats *stats = NULL;
#define X(k,r...) k = 0
  unsigned long long KEYS;
#undef X

  stats = get_current_stats(type, NULL);
  if (stats == NULL)
    return;

/* $ cat /proc/sys/fs/dentry-state
   850986 838906 45 0 0 0

   This file contains six numbers, nr_dentry, nr_unused, age_limit
   (age in seconds), want_pages (pages requested by system) and two
   dummy values.  nr_dentry seems to be 0 all the time.  nr_unused
   seems to be the number of unused dentries.  age_limit is the age in
   seconds after which dcache entries can be reclaimed when memory is
   short and want_pages is non-zero when the kernel has called
   shrink_dcache_pages() and the dcache t pruned yet. */

  unsigned long long dentry_alloc = 0, dentry_free = 0;
  if (path_collect_list("/proc/sys/fs/dentry-state",
                        &dentry_alloc, &dentry_free, NULL) == 2)
    dentry_use = dentry_alloc - dentry_free;

/* $ cat /proc/sys/fs/file-nr
   6080 0 1174404

   Historically, the three values in file-nr denoted the number of
   allocated file handles, the number of allocated but unused file
   handles, and the maximum number of file handles. Linux 2.6 always
   reports 0 as the number of free file handles -- this is not an
   error, it just means that the number of allocated file handles
   exactly matches the number of used file handles. */

  path_collect_single("/proc/sys/fs/file-nr", &file_use);

/* $ cat /proc/sys/fs/inode-state
   168192 59759 0 0 0 0 0

   This file contains seven numbers: nr_inodes, nr_free_inodes,
   preshrink and four dummy values.  nr_inodes is the number of inodes
   the system has allocated.  This can be slightly more than inode-max
   because Linux allocates them one page full at a time.
   nr_free_inodes represents the number of free inodes.  preshrink is
   non-zero when the nr_inodes > inode-max and the system needs to
   prune the inode list instead of allocating more. */

  unsigned long long inode_alloc = 0, inode_free = 0;
  if (path_collect_list("/proc/sys/fs/inode-state",
                        &inode_alloc, &inode_free, NULL) == 2)
    inode_use = inode_alloc - inode_free;

#define X(k,r...) stats_set(stats, #k, k)
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
