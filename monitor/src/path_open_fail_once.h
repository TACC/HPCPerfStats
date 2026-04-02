#ifndef PATH_OPEN_FAIL_ONCE_H
#define PATH_OPEN_FAIL_ONCE_H

#include <dirent.h>
#include <stdio.h>

#include "fileio.h"

int path_open_is_skipped(const char *path);
void path_open_record_failure_once(const char *path);
/* Record path as failed without logging (for non-open errors that should not repeat). */
void path_fail_mark(const char *path);

static inline DIR *path_opendir_or_record_fail(const char *path)
{
  if (path_open_is_skipped(path))
    return NULL;
  DIR *d = opendir(path);
  if (d == NULL)
    path_open_record_failure_once(path);
  return d;
}

static inline FILE *path_file_fopen_read(const char *path)
{
  if (path_open_is_skipped(path))
    return NULL;
  FILE *f = file_fopen_read(path);
  if (f == NULL)
    path_open_record_failure_once(path);
  return f;
}

static inline FILE *path_file_fopen_append(const char *path)
{
  if (path_open_is_skipped(path))
    return NULL;
  FILE *f = file_fopen_append(path);
  if (f == NULL)
    path_open_record_failure_once(path);
  return f;
}

#endif
