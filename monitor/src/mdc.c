/* lustre_mdc — Lustre metadata client stats from /proc/fs/lustre/mdc. */
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
#include "lustre_mdc.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"

#define MDC_DIR_PATH "/proc/fs/lustre/mdc"

static const char *const mdc_stats_names[] = {
  "md_stats",
  "stats",
};

/* Map modern/legacy opcode names onto existing lustre_mdc KEYS. */
static const char *mdc_map_key(const char *key)
{
  if (key == NULL)
    return NULL;
  if (strcmp(key, "req_waittime") == 0)
    return key;
  if (strcmp(key, "close") == 0)
    return "mds_close";
  if (strcmp(key, "getattr") == 0)
    return "mds_getattr";
  if (strcmp(key, "getxattr") == 0)
    return "mds_getxattr";
  if (strcmp(key, "read_page") == 0)
    return "mds_readpage";
  if (strcmp(key, "intent_getattr_async") == 0)
    return "mds_getattr_lock";
  if (strcmp(key, "fsync") == 0)
    return "mds_sync";
  if (strcmp(key, "ldlm_cancel") == 0
      || strcmp(key, "mds_close") == 0
      || strcmp(key, "mds_getattr") == 0
      || strcmp(key, "mds_getattr_lock") == 0
      || strcmp(key, "mds_getxattr") == 0
      || strcmp(key, "mds_readpage") == 0
      || strcmp(key, "mds_statfs") == 0
      || strcmp(key, "mds_sync") == 0
      || strcmp(key, "reqs") == 0
      || strcmp(key, "wait") == 0)
    return key;
  return NULL;
}

static void mdc_apply_stats_line(struct stats *stats, const char *key,
                                 const char *rest)
{
  unsigned long long count = 0;
  unsigned long long sum = 0;
  const char *mapped;
  int n;

  if (stats == NULL || key == NULL || rest == NULL)
    return;

  n = lustre_parse_samples_count(rest, &count, &sum);
  if (n == 0)
    return;

  if (strcmp(key, "req_waittime") == 0) {
    stats_set(stats, "reqs", count);
    stats_set(stats, "wait", sum);
    return;
  }

  mapped = mdc_map_key(key);
  if (mapped != NULL)
    stats_set(stats, mapped, count);
  else
    stats_inc(stats, "reqs", count);
}

static void mdc_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (stats == NULL || d_name == NULL)
    return;

  if (lustre_fopen_obd_named(MDC_DIR_PATH, d_name, mdc_stats_names,
                             sizeof(mdc_stats_names) / sizeof(mdc_stats_names[0]),
                             &path, &file) < 0)
    return;

  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));
  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);

    if (key == NULL || line == NULL)
      continue;
    mdc_apply_stats_line(stats, key, line);
  }

  free(line_buf);
  fclose(file);
  free(path);
}

static void mdc_each(const char *base, const char *name, void *ctx)
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

  mdc_collect_fs(stats, name);
}

static void mdc_collect(struct stats_type *type)
{
  if (type == NULL)
    return;
  sys_iter_for_each(MDC_DIR_PATH, mdc_each, type);
}

struct stats_type mdc_stats_type = {
  .st_name = "lustre_mdc",
  .st_collect = &mdc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
