#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include <stdarg.h>
#include <errno.h>
#include <ctype.h>
#include <fcntl.h>
#include <unistd.h>
#include "stats.h"
#include "trace.h"
#include "collect.h"
#include "string1.h"

#ifndef O_CLOEXEC
#define O_CLOEXEC 0
#endif

/* Stack-sized reads for sysctl-style single-line /proc and /sys files (Tier C #8). */
#define COLLECT_SMALL_BUF 4096
/* Cap for path_collect_key_value (e.g. /proc/vmstat) to bound memory. */
#define COLLECT_SLURP_MAX (1u << 20)

/* Read up to bufsz-1 bytes into buf, NUL-terminate, close fd. Returns 0 or -1. */
static int collect_read_small(const char *path, char *buf, size_t bufsz, size_t *out_len)
{
  int fd;
  size_t total;

  if (bufsz < 2) {
    ERROR("collect_read_small: buffer too small for `%s'\n", path);
    return -1;
  }

  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    ERROR("cannot open `%s': %m\n", path);
    return -1;
  }

  total = 0;
  while (total + 1 < bufsz) {
    ssize_t n = read(fd, buf + total, bufsz - 1 - total);
    if (n < 0) {
      ERROR("cannot read `%s': %m\n", path);
      close(fd);
      return -1;
    }
    if (n == 0)
      break;
    total += (size_t)n;
  }
  close(fd);
  buf[total] = '\0';
  *out_len = total;
  return 0;
}

/* Read entire file into a malloc'd buffer (NUL-terminated). NULL on failure. */
static char *collect_slurp_file(const char *path)
{
  int fd = -1;
  size_t cap = 8192;
  size_t len = 0;
  char *buf = malloc(cap);

  if (buf == NULL) {
    ERROR("cannot allocate read buffer for `%s': %m\n", path);
    return NULL;
  }

  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    ERROR("cannot open `%s': %m\n", path);
    free(buf);
    return NULL;
  }

  for (;;) {
    if (len + 1 >= cap) {
      if (cap >= COLLECT_SLURP_MAX) {
	ERROR("file `%s' exceeds COLLECT_SLURP_MAX\n", path);
	free(buf);
	close(fd);
	return NULL;
      }
      size_t ncap = cap * 2;
      if (ncap > COLLECT_SLURP_MAX)
	ncap = COLLECT_SLURP_MAX;
      char *nb = realloc(buf, ncap);
      if (nb == NULL) {
	ERROR("cannot grow read buffer for `%s': %m\n", path);
	free(buf);
	close(fd);
	return NULL;
      }
      buf = nb;
      cap = ncap;
    }
    ssize_t n = read(fd, buf + len, cap - len - 1);
    if (n < 0) {
      ERROR("cannot read `%s': %m\n", path);
      free(buf);
      close(fd);
      return NULL;
    }
    if (n == 0)
      break;
    len += (size_t)n;
  }
  close(fd);
  buf[len] = '\0';
  return buf;
}

int path_collect_single(const char *path, unsigned long long *dest)
{
  char buf[COLLECT_SMALL_BUF];
  size_t n;
  unsigned long long val;
  char *end = NULL;

  if (collect_read_small(path, buf, sizeof(buf), &n) < 0)
    return -1;

  errno = 0;
  val = strtoull(buf, &end, 0);
  if (errno != 0 || end == buf) {
    return 0;
  }

  *dest = val;
  return 1;
}

int path_collect_list(const char *path, ...)
{
  char buf[COLLECT_SMALL_BUF];
  size_t n;
  va_list dest_list;
  int rc = 0;

  va_start(dest_list, path);

  if (collect_read_small(path, buf, sizeof(buf), &n) < 0) {
    rc = -1;
    goto out;
  }

  const char *p = buf;
  unsigned long long *dest;
  while ((dest = va_arg(dest_list, unsigned long long *)) != NULL) {
    while (*p != '\0' && isspace((unsigned char)*p))
      p++;
    if (*p == '\0') {
      ERROR("%s: no value\n", path);
      goto out;
    }
    errno = 0;
    char *end = NULL;
    unsigned long long v = strtoull(p, &end, 0);
    if (errno != 0 || end == p) {
      ERROR("%s: no value\n", path);
      goto out;
    }
    *dest = v;
    p = end;
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
  va_start(key_list, stats);

  const char *key;
  while ((key = va_arg(key_list, const char *)) != NULL) {
    char *end = NULL;
    unsigned long long val;

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

    stats_set(stats, key, val);
    str = end;
    rc++;
  }

 out:
  if (errno == 0)
    errno = errno_saved;

  return rc;
}

int str_collect_prefix_key_list(const char *str, struct stats *stats,
				const char *pre, ...)
{
  int rc = 0;
  int errno_saved = errno;
  size_t pre_len = strlen(pre);
  char *key = NULL;
  va_list suf_list;
  va_start(suf_list, pre);

  const char *suf;
  while ((suf = va_arg(suf_list, const char *)) != NULL) {
    size_t suf_len = strlen(suf);
    char *tmp = (char *)realloc(key, pre_len + suf_len + 1);
    if (tmp == NULL) {
      ERROR("cannot allocate key string: %m\n");
      goto out;
    }
    key = tmp;

    memcpy(key, pre, pre_len);
    memcpy(key + pre_len, suf, suf_len);
    key[pre_len + suf_len] = 0;

    TRACE("pre `%s', suf `%s', key `%s'\n", pre, suf, key);

    char *end = NULL;
    unsigned long long val;

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

    stats_set(stats, key, val);
    str = end;
    rc++;
  }

 out:
  free(key);
  if (errno == 0)
    errno = errno_saved;

  return rc;
}

int path_collect_key_list(const char *path, struct stats *stats, ...)
{
  char buf[COLLECT_SMALL_BUF];
  size_t n;
  va_list key_list;
  int rc = 0;
  const char *p;

  va_start(key_list, stats);

  if (collect_read_small(path, buf, sizeof(buf), &n) < 0) {
    rc = -1;
    goto out;
  }

  p = buf;
  const char *key;
  while ((key = va_arg(key_list, const char *)) != NULL) {
    while (*p != '\0' && isspace((unsigned char)*p))
      p++;
    if (*p == '\0') {
      ERROR("%s: no value for key `%s'\n", path, key);
      goto out;
    }
    errno = 0;
    char *end = NULL;
    unsigned long long val = strtoull(p, &end, 0);
    if (errno != 0 || end == p) {
      ERROR("%s: no value for key `%s'\n", path, key);
      goto out;
    }
    p = end;
    stats_set(stats, key, val);
    rc++;
  }

 out:
  va_end(key_list);
  return rc;
}

int path_collect_key_value(const char *path, struct stats *stats)
{
  char *content = collect_slurp_file(path);
  char *ptr;

  if (content == NULL)
    return -1;

  for (ptr = content; *ptr != '\0'; ) {
    char *line = ptr;
    char *nl = strchr(ptr, '\n');
    char *key;
    char *rest;
    unsigned long long val;

    if (nl != NULL) {
      *nl = '\0';
      ptr = nl + 1;
    } else {
      ptr = line + strlen(line);
    }

    rest = line;
    key = wsep(&rest);
    if (key == NULL || rest == NULL)
      continue;

    errno = 0;
    val = strtoull(rest, NULL, 0);
    if (errno == 0)
      stats_set(stats, key, val);
  }

  free(content);
  return 0;
}

int path_collect_key_value_dir(const char *dir_path, struct stats *stats)
{
  int rc = 0;
  DIR *dir = NULL;

  dir = opendir(dir_path);
  if (dir == NULL) {
    ERROR("cannot open `%s': %m\n", dir_path);
    rc = -1;
    goto out;
  }

  struct dirent *ent;
  while ((ent = readdir(dir)) != NULL) {
    char *path = NULL;
    unsigned long long val = 0;

    if (ent->d_name[0] == '.')
      goto next;

    if (asprintf(&path, "%s/%s", dir_path, ent->d_name) < 0) {
      ERROR("cannot allocate path: %m\n");
      goto next;
    }

    if (path_collect_single(path, &val) != 1)
      goto next;

    stats_set(stats, ent->d_name, val);

  next:
    free(path);
  }

 out:
  if (dir != NULL)
    closedir(dir);

  return rc;
}
