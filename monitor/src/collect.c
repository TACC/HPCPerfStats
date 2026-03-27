#include <stdio.h>
#include <stdlib.h>
#include <dirent.h>
#include <stdarg.h>
#include <errno.h>
#include "stats.h"
#include "trace.h"
#include "collect.h"
#include "string1.h"

int path_collect_single(const char *path, unsigned long long *dest)
{
  int rc = 0;
  FILE *file = NULL;
  char file_buf[4096];
  unsigned long long val;

  file = fopen(path, "r");
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    rc = -1;
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  if (fscanf(file, "%llu", &val) == 1) {
    *dest = val;
    rc = 1;
  }

 out:
  if (file != NULL)
    fclose(file);

  return rc;
}

int path_collect_list(const char *path, ...)
{
  int rc = 0;
  FILE *file = NULL;
  char file_buf[4096];
  va_list dest_list;
  va_start(dest_list, path);

  file = fopen(path, "r");
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    rc = -1;
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  unsigned long long *dest;
  while ((dest = va_arg(dest_list, unsigned long long *)) != NULL) {

    if (fscanf(file, "%llu", dest) != 1) {
      ERROR("%s: no value\n", path);
      goto out;
    }
    rc++;
  }

 out:
  if (file != NULL)
    fclose(file);
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
  int rc = 0;
  FILE *file = NULL;
  char file_buf[4096];
  va_list key_list;
  va_start(key_list, stats);

  file = fopen(path, "r");
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    rc = -1;
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  const char *key;
  while ((key = va_arg(key_list, const char *)) != NULL) {
    unsigned long long val;

    if (fscanf(file, "%llu", &val) != 1) {
      ERROR("%s: no value for key `%s'\n", path, key);
      goto out;
    }
    stats_set(stats, key, val);
    rc++;
  }

 out:
  if (file != NULL)
    fclose(file);
  va_end(key_list);

  return rc;
}

int path_collect_key_value(const char *path, struct stats *stats)
{
  int rc = 0;
  FILE *file = NULL;
  char file_buf[4096];
  char *line = NULL;
  size_t line_size = 0;

  file = fopen(path, "r");
  if (file == NULL) {
    ERROR("cannot open `%s': %m\n", path);
    rc = -1;
    goto out;
  }
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  while (getline(&line, &line_size, file) >= 0) {
    char *key, *rest = line;
    unsigned long long val;

    key = wsep(&rest);
    if (key == NULL || rest == NULL)
      continue;

    errno = 0;
    val = strtoull(rest, NULL, 0);
    if (errno == 0)
      stats_set(stats, key, val);
  }

 out:
  free(line);
  if (file != NULL)
    fclose(file);

  return rc;
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
