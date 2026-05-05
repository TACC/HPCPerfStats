#ifndef _PROCFILE_KV_H_
#define _PROCFILE_KV_H_

#include <errno.h>
#include <stdlib.h>

#include "stats.h"
#include "string1.h"

/* Parse a "<key> <value> ..." line into stats: key is the first whitespace-
 * separated token, value is parsed via strtoull on the remainder. The line
 * buffer is modified in place (string1.h wsep semantics).
 *
 * Returns 0 on success or -1 if no value could be parsed. Header-only so
 * the procfile iterator translation unit stays free of stats.c symbols. */
static inline int proc_kv_into_stats(struct stats *stats, char *line)
{
  char *rest = line;
  char *key = wsep(&rest);
  unsigned long long val;
  int saved_errno = errno;

  if (key == NULL || rest == NULL)
    return -1;

  errno = 0;
  val = strtoull(rest, NULL, 0);
  if (errno != 0) {
    errno = saved_errno;
    return -1;
  }

  errno = saved_errno;
  stats_set(stats, key, val);
  return 0;
}

#endif
