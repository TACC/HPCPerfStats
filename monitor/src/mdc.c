#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include "stats.h"
#include "fileio.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "path_open_fail_once.h"
#include "procfile_parse.h"
#include "sys_iter.h"

#define MDC_DIR_PATH "/proc/fs/lustre/mdc"

#define KEYS \
  X(ldlm_cancel, "E", ""), \
  X(mds_close, "E", ""), \
  X(mds_getattr, "E", ""), \
  X(mds_getattr_lock, "E", ""), \
  X(mds_getxattr, "E", ""), \
  X(mds_readpage, "E", ""), \
  X(mds_statfs, "E", ""), \
  X(mds_sync, "E", ""), \
  X(reqs, "E", ""), \
  X(wait, "E,U=us", "")

static int mdc_stats_line_cb(char *line, void *ctx)
{
  struct stats *stats = (struct stats *)ctx;
  char *rest = line;
  char *key = wsep(&rest);
  unsigned long long count = 0, sum = 0;

  if (key == NULL || rest == NULL)
    return 0;

  if (sscanf(rest, "%llu samples %*s %*u %*u %llu", &count, &sum) != 2)
    return 0;

  if (strcmp(key, "req_waittime") == 0) {
    stats_set(stats, "reqs", count);
    stats_set(stats, "wait", sum);
  } else {
    stats_set(stats, key, count);
  }
  return 0;
}

static void mdc_collect_fs(struct stats *stats, const char *d_name)
{
  char *path = NULL;

  if (asprintf(&path, "%s/%s/stats", MDC_DIR_PATH, d_name) < 0) {
    ERROR("cannot create path: %m\n");
    return;
  }
  procfile_for_each_line(path, mdc_stats_line_cb, stats);
  free(path);
}

static void mdc_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  const char *mnt = lustre_obd_to_mnt(name);
  struct stats *stats;

  (void)base;
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
  sys_iter_for_each(MDC_DIR_PATH, mdc_each, type);
}

struct stats_type mdc_stats_type = {
  .st_name = "mdc",
  .st_collect = &mdc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
