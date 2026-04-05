#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "path_open_fail_once.h"
#include "trace.h"

static char **failed_keys;
static size_t failed_n;
static size_t failed_cap;

static int failed_has(const char *path)
{
  size_t i;

  for (i = 0; i < failed_n; i++)
    if (strcmp(failed_keys[i], path) == 0)
      return 1;
  return 0;
}

static int failed_add(const char *path)
{
  char *k;
  char **more;

  if (failed_has(path))
    return 0;
  k = strdup(path);
  if (k == NULL)
    return -1;
  if (failed_n >= failed_cap) {
    size_t ncap = failed_cap ? failed_cap * 2 : 32u;
    more = (char **)realloc(failed_keys, ncap * sizeof(*more));
    if (more == NULL) {
      free(k);
      return -1;
    }
    failed_keys = more;
    failed_cap = ncap;
  }
  failed_keys[failed_n++] = k;
  return 0;
}

int path_open_is_skipped(const char *path)
{
  if (path == NULL)
    return 0;
  return failed_has(path);
}

void path_fail_mark(const char *path)
{
  if (path == NULL)
    return;
  (void)failed_add(path);
}

void path_open_record_failure_once(const char *path)
{
  int err = errno;

  if (path == NULL)
    return;

  if (failed_has(path)) {
    errno = err;
    return;
  }

  errno = err;
  /* Missing sysfs/proc trees (no Lustre, no IB, …) are normal on many hosts. */
  if (err == ENOENT)
    TRACE("cannot open `%s': %m\n", path);
  else
    ERROR("cannot open `%s': %m\n", path);

  errno = err;
  (void)failed_add(path);
}
