/* lustre_osc — Lustre object storage client stats from /proc/fs/lustre/osc. */
#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"

#define OSC_DIR_PATH "/proc/fs/lustre/osc"

#define KEYS \
  X(read_bytes, "E,U=B", ""), \
  X(write_bytes, "E,U=B", ""), \
  X(ost_destroy, "E", ""), \
  X(ost_punch, "E", ""), \
  X(ost_read, "E", ""), \
  X(ost_setattr, "E", ""), \
  X(ost_statfs, "E", ""), \
  X(ost_write, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")

static void osc_apply_stats_line(struct stats *stats, const char *key, const char *line)
{
  unsigned long long count = 0;
  unsigned long long sum = 0;

  if (stats == NULL || key == NULL || line == NULL)
    return;
  if (sscanf(line, "%llu samples %*s %*u %*u %llu", &count, &sum) != 2)
    return;

  if (strcmp(key, "req_waittime") == 0) {
    stats_inc(stats, "reqs", count);
    stats_inc(stats, "wait", sum);
  } else if (strcmp(key, "read_bytes") == 0 || strcmp(key, "write_bytes") == 0) {
    stats_inc(stats, key, sum);
  } else {
    stats_inc(stats, key, count);
  }
}

static void osc_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (stats == NULL || d_name == NULL)
    return;

  if (asprintf(&path, "%s/%s/stats", OSC_DIR_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    return;
  }

  file = path_file_fopen_read(path);
  if (file == NULL)
    goto out;
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);

    if (key == NULL || line == NULL)
      continue;
    osc_apply_stats_line(stats, key, line);
  }

 out:
  free(line_buf);
  if (file != NULL)
    fclose(file);
  free(path);
}

static void osc_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *) ctx;
  const char *mnt;
  struct stats *stats;

  (void) base;
  if (type == NULL || name == NULL)
    return;

  mnt = lustre_obd_to_mnt(name);
  if (mnt == NULL)
    return;

  TRACE("d_name `%s', mnt `%s'\n", name, mnt);

  stats = get_current_stats(type, mnt);
  if (stats == NULL)
    return;

  osc_collect_fs(stats, name);
}

static void osc_collect(struct stats_type *type)
{
  if (type == NULL)
    return;
  sys_iter_for_each(OSC_DIR_PATH, osc_each, type);
}

struct stats_type osc_stats_type = {
  .st_name = "lustre_osc",
  .st_collect = &osc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
