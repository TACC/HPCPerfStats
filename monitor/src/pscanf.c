/* Bounded path scan: read file via path_read, then vsscanf. */
#include "pscanf.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

#include "path_read.h"

#define PSCANF_SMALL_BUF 4096

static const struct path_read_opts pscanf_read_opts = {
    .skip_known_bad = 0,
    .report_errors = 0,
    .detect_overflow = 1,
};

int pscanf(const char *path, const char *fmt, ...)
{
  char sbuf[PSCANF_SMALL_BUF];
  char *buf = sbuf;
  size_t len;
  va_list ap;
  int n;
  int small_rc;

  if (path == NULL || fmt == NULL) {
    return -1;
  }

  small_rc = path_read_small(path, sbuf, sizeof(sbuf), &len, &pscanf_read_opts);
  if (small_rc < 0)
    return -1;
  if (small_rc != 0) {
    if (path_read_alloc(path, &buf, &len, &pscanf_read_opts) < 0)
      return -1;
  }

  va_start(ap, fmt);
  n = vsscanf(buf, fmt, ap);
  va_end(ap);
  if (buf != sbuf)
    free(buf);
  return n;
}
