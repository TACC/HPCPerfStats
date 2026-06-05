/* Path and string collectors that parse kernel text into stats metrics. */
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collect.h"
#include "path_open_fail_once.h"
#include "path_read.h"
#include "stats.h"
#include "string1.h"
#include "trace.h"

#define COLLECT_SMALL_BUF 4096

static collect_key_active_fn g_key_active_hook;
static void *g_key_active_hook_ctx;

void collect_set_key_active_hook(collect_key_active_fn fn, void *ctx)
{
  g_key_active_hook = fn;
  g_key_active_hook_ctx = ctx;
}

static int collect_key_is_active(collect_key_active_fn active, void *ctx,
                                 struct stats *stats, const char *key)
{
  if (active == NULL)
    return 1;
  return active(ctx, stats, key);
}

static const struct path_read_opts collect_read_opts = {
  .skip_known_bad  = 1,
  .report_errors   = 1,
  .detect_overflow = 0,
};

static int collect_read_small(const char *path, char *buf, size_t bufsz, size_t *out_len)
{
  return path_read_small(path, buf, bufsz, out_len, &collect_read_opts);
}

static char *collect_slurp_file(const char *path)
{
  char *buf = NULL;
  size_t len = 0;

  if (path == NULL)
    return NULL;

  if (path_read_alloc(path, &buf, &len, &collect_read_opts) < 0)
    return NULL;
  return buf;
}

static const char *collect_skip_ws(const char *p)
{
  while (*p != '\0' && isspace((unsigned char) *p))
    p++;
  return p;
}

static int collect_parse_one_ull(const char **p, unsigned long long *out)
{
  char *end = NULL;
  unsigned long long v;

  *p = collect_skip_ws(*p);
  if (**p == '\0')
    return 0;

  errno = 0;
  v = strtoull(*p, &end, 0);
  if (errno != 0 || end == *p)
    return 0;

  *out = v;
  *p = end;
  return 1;
}

int path_collect_single(const char *path, unsigned long long *dest)
{
  char buf[COLLECT_SMALL_BUF];
  size_t n;
  const char *p;

  if (path == NULL || dest == NULL)
    return -1;

  if (collect_read_small(path, buf, sizeof(buf), &n) < 0)
    return -1;

  p = buf;
  if (!collect_parse_one_ull(&p, dest))
    return 0;

  return 1;
}

int path_collect_list(const char *path, ...)
{
  char buf[COLLECT_SMALL_BUF];
  size_t n;
  va_list dest_list;
  int rc = 0;
  const char *p;

  if (path == NULL)
    return -1;

  va_start(dest_list, path);

  if (collect_read_small(path, buf, sizeof(buf), &n) < 0) {
    rc = -1;
    goto out;
  }

  p = buf;
  for (;;) {
    unsigned long long *dest = va_arg(dest_list, unsigned long long *);

    if (dest == NULL)
      break;

    if (!collect_parse_one_ull(&p, dest)) {
      ERROR("%s: no value\n", path);
      goto out;
    }
    rc++;
  }

 out:
  va_end(dest_list);
  return rc;
}

int str_collect_key_list(const char *str, struct stats *stats, ...)
{
  int rc = 0;
  int errno_saved = errno;
  va_list key_list;

  if (str == NULL || stats == NULL)
    return -1;

  va_start(key_list, stats);

  for (;;) {
    const char *key = va_arg(key_list, const char *);
    char *end = NULL;
    unsigned long long val;

    if (key == NULL)
      break;

    errno = 0;
    val = strtoull(str, &end, 0);
    if (errno != 0) {
      ERROR("cannot convert str `%s' for key `%s': %m\n", str, key);
      goto out;
    }

    if (str == end) {
      ERROR("no value in str `%s' for key `%s'\n", str, key);
      goto out;
    }

    if (collect_key_is_active(g_key_active_hook, g_key_active_hook_ctx, stats, key))
      stats_set(stats, key, val);
    str = end;
    rc++;
  }

 out:
  va_end(key_list);
  if (errno == 0)
    errno = errno_saved;

  return rc;
}

static char *collect_build_prefixed_key(const char *pre, const char *suf,
                                        char *key, size_t *key_cap)
{
  size_t pre_len = strlen(pre);
  size_t suf_len = strlen(suf);
  size_t need = pre_len + suf_len + 1;
  char *tmp;

  if (need > *key_cap) {
    tmp = (char *) realloc(key, need);
    if (tmp == NULL)
      return NULL;
    key = tmp;
    *key_cap = need;
  }

  if (snprintf(key, need, "%s%s", pre, suf) >= (int) need)
    return NULL;

  return key;
}

int str_collect_prefix_key_list(const char *str, struct stats *stats,
                                const char *pre, ...)
{
  int rc = 0;
  int errno_saved = errno;
  char *key = NULL;
  size_t key_cap = 0;
  va_list suf_list;

  if (str == NULL || stats == NULL || pre == NULL)
    return -1;

  va_start(suf_list, pre);

  for (;;) {
    const char *suf = va_arg(suf_list, const char *);
    char *end = NULL;
    unsigned long long val;

    if (suf == NULL)
      break;

    key = collect_build_prefixed_key(pre, suf, key, &key_cap);
    if (key == NULL) {
      ERROR("cannot allocate key string: %m\n");
      goto out;
    }

    TRACE("pre `%s', suf `%s', key `%s'\n", pre, suf, key);

    errno = 0;
    val = strtoull(str, &end, 0);
    if (errno != 0) {
      ERROR("cannot convert str `%s' for key `%s': %m\n", str, key);
      goto out;
    }

    if (str == end) {
      ERROR("no value in str `%s' for key `%s'\n", str, key);
      goto out;
    }

    if (collect_key_is_active(g_key_active_hook, g_key_active_hook_ctx, stats, key))
      stats_set(stats, key, val);
    str = end;
    rc++;
  }

 out:
  free(key);
  va_end(suf_list);
  if (errno == 0)
    errno = errno_saved;

  return rc;
}

static int vpath_collect_key_list(const char *path, struct stats *stats,
                                  collect_key_active_fn active, void *ctx,
                                  va_list key_list)
{
  char buf[COLLECT_SMALL_BUF];
  size_t n;
  int rc = 0;
  const char *p;

  if (collect_read_small(path, buf, sizeof(buf), &n) < 0)
    return -1;

  p = buf;
  for (;;) {
    const char *key = va_arg(key_list, const char *);
    unsigned long long val;

    if (key == NULL)
      break;

    /* Positional format: always consume the value to stay aligned, but only
     * store keys active in the current collection phase. */
    if (!collect_parse_one_ull(&p, &val)) {
      ERROR("%s: no value for key `%s'\n", path, key);
      return rc;
    }
    if (collect_key_is_active(active, ctx, stats, key))
      stats_set(stats, key, val);
    rc++;
  }

  return rc;
}

int path_collect_key_list(const char *path, struct stats *stats, ...)
{
  va_list key_list;
  int rc;

  if (path == NULL || stats == NULL)
    return -1;

  va_start(key_list, stats);
  rc = vpath_collect_key_list(path, stats, g_key_active_hook,
                              g_key_active_hook_ctx, key_list);
  va_end(key_list);
  return rc;
}

int path_collect_key_list_filtered(const char *path, struct stats *stats,
                                   collect_key_active_fn active, void *ctx, ...)
{
  va_list key_list;
  int rc;

  if (path == NULL || stats == NULL)
    return -1;

  va_start(key_list, ctx);
  rc = vpath_collect_key_list(path, stats, active, ctx, key_list);
  va_end(key_list);
  return rc;
}

static void collect_apply_key_value_line(char *line, struct stats *stats,
                                         collect_key_active_fn active, void *ctx)
{
  char *rest;
  char *key;
  unsigned long long val;

  rest = line;
  key = wsep(&rest);
  if (key == NULL || rest == NULL)
    return;

  if (!collect_key_is_active(active, ctx, stats, key))
    return;

  errno = 0;
  val = strtoull(rest, NULL, 0);
  if (errno == 0)
    stats_set(stats, key, val);
}

int path_collect_key_value_filtered(const char *path, struct stats *stats,
                                    collect_key_active_fn active, void *ctx)
{
  char *content;
  char *ptr;

  if (path == NULL || stats == NULL)
    return -1;

  content = collect_slurp_file(path);
  if (content == NULL)
    return -1;

  for (ptr = content; *ptr != '\0'; ) {
    char *line = ptr;
    char *nl = strchr(ptr, '\n');

    if (nl != NULL) {
      *nl = '\0';
      ptr = nl + 1;
    } else {
      ptr = line + strlen(line);
    }

    collect_apply_key_value_line(line, stats, active, ctx);
  }

  free(content);
  return 0;
}

int path_collect_key_value(const char *path, struct stats *stats)
{
  return path_collect_key_value_filtered(path, stats, g_key_active_hook,
                                         g_key_active_hook_ctx);
}

static int collect_one_dir_entry(const char *dir_path, struct dirent *ent,
                                 struct stats *stats,
                                 collect_key_active_fn active, void *ctx)
{
  char *path = NULL;
  unsigned long long val = 0;

  if (ent->d_name[0] == '.')
    return 0;

  /* Skip the per-file open/read entirely for keys inactive this phase. */
  if (!collect_key_is_active(active, ctx, stats, ent->d_name))
    return 0;

  if (asprintf(&path, "%s/%s", dir_path, ent->d_name) < 0) {
    ERROR("cannot allocate path: %m\n");
    return 0;
  }

  if (path_collect_single(path, &val) == 1)
    stats_set(stats, ent->d_name, val);

  free(path);
  return 0;
}

int path_collect_key_value_dir_filtered(const char *dir_path, struct stats *stats,
                                        collect_key_active_fn active, void *ctx)
{
  int rc = 0;
  DIR *dir = NULL;
  struct dirent *ent;

  if (dir_path == NULL || stats == NULL)
    return -1;

  dir = path_opendir_or_record_fail(dir_path);
  if (dir == NULL)
    return -1;

  while ((ent = readdir(dir)) != NULL)
    (void) collect_one_dir_entry(dir_path, ent, stats, active, ctx);

  closedir(dir);
  return rc;
}

int path_collect_key_value_dir(const char *dir_path, struct stats *stats)
{
  return path_collect_key_value_dir_filtered(dir_path, stats, g_key_active_hook,
                                             g_key_active_hook_ctx);
}
