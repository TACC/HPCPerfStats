/* lustre_osc — Lustre object storage client stats from /proc/fs/lustre/osc. */
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
#include "lustre_osc.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"

#define OSC_DIR_PATH "/proc/fs/lustre/osc"

static const char *const osc_stats_names[] = {
    "osc_stats",
    "stats",
};

static void osc_apply_stats_line(struct stats *stats, const char *key, const char *rest)
{
  unsigned long long count = 0;
  unsigned long long sum = 0;
  int n;

  if (stats == NULL || key == NULL || rest == NULL)
    return;

  n = lustre_parse_samples_count(rest, &count, &sum);
  if (n == 0)
    return;

  if (strcmp(key, "req_waittime") == 0) {
    stats_inc(stats, "reqs", count);
    stats_inc(stats, "wait", sum);
  } else if (strcmp(key, "read_bytes") == 0 || strcmp(key, "lockless_read_bytes") == 0) {
    stats_inc(stats, "read_bytes", n == 2 ? sum : count);
  } else if (strcmp(key, "write_bytes") == 0 || strcmp(key, "lockless_write_bytes") == 0) {
    stats_inc(stats, "write_bytes", n == 2 ? sum : count);
  } else if (strcmp(key, "ost_destroy") == 0 || strcmp(key, "ost_punch") == 0 ||
             strcmp(key, "ost_read") == 0 || strcmp(key, "ost_setattr") == 0 ||
             strcmp(key, "ost_statfs") == 0 || strcmp(key, "ost_write") == 0 ||
             strcmp(key, "reqs") == 0 || strcmp(key, "wait") == 0) {
    stats_inc(stats, key, count);
  } else {
    stats_inc(stats, "reqs", count);
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

  if (lustre_fopen_obd_named(OSC_DIR_PATH, d_name, osc_stats_names,
                             sizeof(osc_stats_names) / sizeof(osc_stats_names[0]), &path,
                             &file) < 0)
    return;

  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));
  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);

    if (key == NULL || line == NULL)
      continue;
    osc_apply_stats_line(stats, key, line);
  }

  free(line_buf);
  fclose(file);
  free(path);
}

static void osc_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  const char *mnt;
  struct stats *stats;

  (void)base;
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
