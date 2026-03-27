#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"

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

static void llite_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (asprintf(&path, "%s/%s/stats", LLITE_DIR_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    goto out;
  }

  file = file_fopen_read(path);
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  // $ cat /proc/fs/lustre/llite/scratch-ffff81019a4eb000/stats
  // snapshot_time             1301585789.189183 secs.usecs
  // dirty_pages_hits          276 samples [regs]
  // dirty_pages_misses        57841029 samples [regs]
  // read_bytes                449131 samples [bytes] 1 23553884 101262334767
  // write_bytes               201770 samples [bytes] 1 20971520 236938468092
  // brw_read                  13 samples [pages] 4096 4096 53248
  // ioctl                     8050 samples [regs]

  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);
    if (key == NULL || line == NULL)
      continue;

    unsigned long long count = 0, sum = 0;
    int n = sscanf(line, "%llu samples %*s %*u %*u %llu", &count, &sum);
    if (n == 1)
      stats_set(stats, key, count);
    else if (n == 2)
      stats_set(stats, key, sum);
  }

 out:
  free(line_buf);
  if (file != NULL)
    fclose(file);
  free(path);
}

static void llite_collect(struct stats_type *type)
{
  const char *llite_dir_path = LLITE_DIR_PATH;
  DIR *llite_dir = NULL;

  llite_dir = opendir(llite_dir_path);
  if (llite_dir == NULL) {
    ERROR("cannot open `%s': %m\n", llite_dir_path);
    goto out;
  }

  struct dirent *de;
  while ((de = readdir(llite_dir)) != NULL) {
    struct stats *stats = NULL;
    const char *mnt;

    if (de->d_type != DT_DIR || *de->d_name == '.')
      continue;

    mnt = lustre_obd_to_mnt(de->d_name);
    if (mnt == NULL)
      continue;

    TRACE("d_name `%s', mnt `%s'\n", de->d_name, mnt);

    stats = get_current_stats(type, mnt);
    if (stats == NULL)
      continue;

    llite_collect_fs(stats, de->d_name);
  }

 out:
  if (llite_dir != NULL)
    closedir(llite_dir);
}

struct stats_type llite_stats_type = {
  .st_name = "llite",
  .st_collect = &llite_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
