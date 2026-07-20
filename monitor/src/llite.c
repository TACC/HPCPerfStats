/* lustre_llite — VFS client opcodes (proc) + capacity gauges (sysfs). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "lustre_proc_stats.h"
#include "lustre_llite.h"
#include "path_open_fail_once.h"
#include "pscanf.h"
#include "sys_iter.h"

#define LLITE_PROC_PATH "/proc/fs/lustre/llite"
#define LLITE_SYSFS_PATH "/sys/fs/lustre/llite"

struct llite_opcode_map {
  const char *src;
  const char *dst;
  int use_sum; /* 1: prefer sample sum (bytes); 0: use count */
};

static const struct llite_opcode_map llite_opcode_map[] = {
    {"read", "vfs_read_ops", 0},
    {"write", "vfs_write_ops", 0},
    {"read_bytes", "vfs_read_bytes", 1},
    {"write_bytes", "vfs_write_bytes", 1},
    {"direct_read", "vfs_direct_read_bytes", 1},
    {"direct_write", "vfs_direct_write_bytes", 1},
    {"osc_read", "vfs_osc_read_bytes", 1},
    {"osc_write", "vfs_osc_write_bytes", 1},
    {"dirty_pages_hits", "vfs_dirty_page_hits", 0},
    {"dirty_pages_misses", "vfs_dirty_page_misses", 0},
    {"ioctl", "vfs_ioctl_ops", 0},
    {"open", "vfs_open_ops", 0},
    {"close", "vfs_close_ops", 0},
    {"mmap", "vfs_mmap_ops", 0},
    {"seek", "vfs_seek_ops", 0},
    {"fsync", "vfs_fsync_ops", 0},
    {"setattr", "vfs_setattr_ops", 0},
    {"truncate", "vfs_truncate_ops", 0},
    {"flock", "vfs_flock_ops", 0},
    {"getattr", "vfs_getattr_ops", 0},
    {"statfs", "vfs_statfs_ops", 0},
    {"alloc_inode", "vfs_alloc_inode_ops", 0},
    {"setxattr", "vfs_setxattr_ops", 0},
    {"getxattr", "vfs_getxattr_ops", 0},
    {"listxattr", "vfs_listxattr_ops", 0},
    {"removexattr", "vfs_removexattr_ops", 0},
    {"inode_permission", "vfs_inode_permission_ops", 0},
    {"readdir", "vfs_readdir_ops", 0},
    {"create", "vfs_create_ops", 0},
    {"lookup", "vfs_lookup_ops", 0},
    {"link", "vfs_link_ops", 0},
    {"unlink", "vfs_unlink_ops", 0},
    {"symlink", "vfs_symlink_ops", 0},
    {"mkdir", "vfs_mkdir_ops", 0},
    {"rmdir", "vfs_rmdir_ops", 0},
    {"mknod", "vfs_mknod_ops", 0},
    {"rename", "vfs_rename_ops", 0},
};

static const char *llite_map_opcode(const char *key, int *use_sum)
{
  size_t i;

  if (key == NULL || use_sum == NULL)
    return NULL;
  for (i = 0; i < sizeof(llite_opcode_map) / sizeof(llite_opcode_map[0]); i++) {
    if (strcmp(key, llite_opcode_map[i].src) == 0) {
      *use_sum = llite_opcode_map[i].use_sum;
      return llite_opcode_map[i].dst;
    }
    if (strcmp(key, llite_opcode_map[i].dst) == 0) {
      *use_sum = llite_opcode_map[i].use_sum;
      return llite_opcode_map[i].dst;
    }
  }
  return NULL;
}

static void llite_apply_stats_line(struct stats *stats, const char *key, const char *rest)
{
  unsigned long long count = 0;
  unsigned long long sum = 0;
  const char *mapped;
  int use_sum = 0;
  int n;

  if (stats == NULL || key == NULL || rest == NULL)
    return;

  n = lustre_parse_samples_count(rest, &count, &sum);
  if (n == 0)
    return;

  mapped = llite_map_opcode(key, &use_sum);
  if (mapped == NULL)
    return;

  if (use_sum && n == 2)
    stats_set(stats, mapped, sum);
  else
    stats_set(stats, mapped, count);
}

static void llite_collect_proc_stats(struct stats *stats, const char *d_name)
{
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (stats == NULL || d_name == NULL)
    return;
  if (asprintf(&path, "%s/%s/stats", LLITE_PROC_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    return;
  }
  file = file_fopen_read(path);
  if (file == NULL)
    goto out;

  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));
  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);

    if (key == NULL || line == NULL)
      continue;
    llite_apply_stats_line(stats, key, line);
  }

out:
  free(line_buf);
  if (file != NULL)
    fclose(file);
  free(path);
}

static void llite_collect_sysfs_capacity(struct stats *stats, const char *d_name)
{
  char path[512];
  unsigned long long kbytes = 0;
  unsigned long long files = 0;

  if (stats == NULL || d_name == NULL)
    return;

  snprintf(path, sizeof(path), "%s/%s/kbytestotal", LLITE_SYSFS_PATH, d_name);
  if (pscanf(path, "%llu", &kbytes) == 1)
    stats_set(stats, "fs_bytes_total", kbytes * 1024ULL);

  snprintf(path, sizeof(path), "%s/%s/kbytesfree", LLITE_SYSFS_PATH, d_name);
  if (pscanf(path, "%llu", &kbytes) == 1)
    stats_set(stats, "fs_bytes_free", kbytes * 1024ULL);

  snprintf(path, sizeof(path), "%s/%s/kbytesavail", LLITE_SYSFS_PATH, d_name);
  if (pscanf(path, "%llu", &kbytes) == 1)
    stats_set(stats, "fs_bytes_avail", kbytes * 1024ULL);

  snprintf(path, sizeof(path), "%s/%s/filestotal", LLITE_SYSFS_PATH, d_name);
  if (pscanf(path, "%llu", &files) == 1)
    stats_set(stats, "fs_files_total", files);

  snprintf(path, sizeof(path), "%s/%s/filesfree", LLITE_SYSFS_PATH, d_name);
  if (pscanf(path, "%llu", &files) == 1)
    stats_set(stats, "fs_files_free", files);
}

static void llite_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  const char *mnt;
  struct stats *stats;
  int is_sysfs;

  if (type == NULL || name == NULL || base == NULL)
    return;

  mnt = lustre_obd_to_mnt(name);
  if (mnt == NULL)
    return;

  TRACE("d_name `%s', mnt `%s' (base `%s')\n", name, mnt, base);

  stats = get_current_stats(type, mnt);
  if (stats == NULL)
    return;

  is_sysfs = (strcmp(base, LLITE_SYSFS_PATH) == 0);
  if (is_sysfs)
    llite_collect_sysfs_capacity(stats, name);
  else
    llite_collect_proc_stats(stats, name);
}

static void llite_collect(struct stats_type *type)
{
  if (type == NULL)
    return;
  sys_iter_for_each(LLITE_PROC_PATH, llite_each, type);
  sys_iter_for_each(LLITE_SYSFS_PATH, llite_each, type);
}

struct stats_type llite_stats_type = {
    .st_name = "lustre_llite",
    .st_collect = &llite_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
