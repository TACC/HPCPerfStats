/* lustre_osc - Lustre object storage client stats from /proc/fs/lustre/osc. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "stats.h"
#include "trace.h"
#include "string1.h"
#include "lustre_obd_to_mnt.h"
#include "lustre_proc_stats.h"
#include "lustre_osc.h"
#include "sys_iter.h"

#define OSC_DIR_PATH "/proc/fs/lustre/osc"

static const char *const osc_stats_names[] = {
    "osc_stats",
    "stats",
};

#define X(k, o, d) #k
static const char *const osc_keys[] = {KEYS};
#undef X
#define OSC_NKEYS (sizeof(osc_keys) / sizeof(osc_keys[0]))

static size_t osc_key_index(const char *key)
{
  size_t i = 0;

  while (i < OSC_NKEYS && strcmp(osc_keys[i], key) != 0)
    i++;
  return i;
}

/* Adds one stats line to `life`, the lifetime counters of one OSC indexed like osc_keys. */
static void osc_apply_stats_line(unsigned long long *life, const char *key, const char *rest)
{
  unsigned long long count = 0;
  unsigned long long sum = 0;
  size_t i;
  int n;

  n = lustre_parse_samples_count(rest, &count, &sum);
  if (n == 0)
    return;

  if (strcmp(key, "req_waittime") == 0) {
    life[osc_key_index("reqs")] += count;
    life[osc_key_index("wait")] += sum;
  } else if (strcmp(key, "read_bytes") == 0 || strcmp(key, "lockless_read_bytes") == 0) {
    life[osc_key_index("read_bytes")] += n == 2 ? sum : count;
  } else if (strcmp(key, "write_bytes") == 0 || strcmp(key, "lockless_write_bytes") == 0) {
    life[osc_key_index("write_bytes")] += n == 2 ? sum : count;
  } else if ((i = osc_key_index(key)) < OSC_NKEYS) {
    life[i] += count;
  } else {
    life[osc_key_index("reqs")] += count;
  }
}

void lustre_osc_collect_obd(struct stats *stats, const char *dir, const char *d_name)
{
  unsigned long long life[OSC_NKEYS] = {0};
  char *path = NULL;
  FILE *file = NULL;
  char file_buf[4096];
  char *line_buf = NULL;
  size_t line_buf_size = 0;

  if (stats == NULL || dir == NULL || d_name == NULL)
    return;

  if (lustre_fopen_obd_named(dir, d_name, osc_stats_names,
                             sizeof(osc_stats_names) / sizeof(osc_stats_names[0]), &path,
                             &file) < 0) {
    enum stats_sentinel code = stats_sentinel_for_errno(errno);

    for (size_t i = 0; i < OSC_NKEYS; i++)
      stats_set_missing(stats, osc_keys[i], code);
    return;
  }

  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));
  while (getline(&line_buf, &line_buf_size, file) >= 0) {
    char *line = line_buf;
    char *key = wsep(&line);

    if (key == NULL || line == NULL)
      continue;
    osc_apply_stats_line(life, key, line);
  }

  free(line_buf);
  fclose(file);
  free(path);
  /* Lustre lists only non-zero counters, so an omitted key contributes 0. */
  for (size_t i = 0; i < OSC_NKEYS; i++)
    stats_accumulate(stats, osc_keys[i], life[i]);
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

  lustre_osc_collect_obd(stats, OSC_DIR_PATH, name);
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
