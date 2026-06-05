/* Incremental growable buffer append for stats_buffer RabbitMQ payloads. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "stats_buffer_data_append.h"

static int stats_buffer_data_ensure_cap(char **data, size_t *cap, size_t need)
{
  size_t ncap;

  if (data == NULL || cap == NULL)
    return -1;

  if (need <= *cap)
    return 0;

  ncap = *cap ? *cap : 64;
  while (ncap < need)
    ncap *= 2;

  {
    char *q = realloc(*data, ncap);

    if (q == NULL)
      return -1;
    *data = q;
    *cap = ncap;
  }
  return 0;
}

int stats_buffer_data_append_bytes(char **data, size_t *len, size_t *cap,
                                   const void *p, size_t n)
{
  size_t need;

  if (data == NULL || len == NULL || cap == NULL)
    return -1;

  need = *len + n + 1;
  if (stats_buffer_data_ensure_cap(data, cap, need) < 0)
    return -1;

  if (n > 0 && p != NULL)
    memcpy(*data + *len, p, n);
  *len += n;
  (*data)[*len] = '\0';
  return 0;
}

static int stats_buffer_data_try_vfmt_inplace(char **data, size_t *len,
                                              size_t *cap, const char *fmt,
                                              va_list ap)
{
  size_t avail;
  va_list aq;
  int n;

  avail = *cap - *len;
  if (avail <= 1)
    return 1;

  va_copy(aq, ap);
  n = vsnprintf(*data + *len, avail, fmt, aq);
  va_end(aq);
  if (n < 0)
    return -1;
  if ((size_t) n < avail) {
    *len += (size_t) n;
    return 0;
  }
  return 1;
}

static int stats_buffer_data_append_vfmt_grow(char **data, size_t *len,
                                              size_t *cap, const char *fmt,
                                              va_list ap, size_t add)
{
  size_t need = *len + add + 1;
  va_list aq;

  if (stats_buffer_data_ensure_cap(data, cap, need) < 0)
    return -1;

  va_copy(aq, ap);
  vsnprintf(*data + *len, *cap - *len, fmt, aq);
  va_end(aq);
  *len += add;
  return 0;
}

int stats_buffer_data_append_vfmt(char **data, size_t *len, size_t *cap,
                                  const char *fmt, va_list ap)
{
  int try_rc;
  size_t add;
  va_list aq;
  int n;

  if (data == NULL || len == NULL || cap == NULL || fmt == NULL)
    return -1;

  try_rc = stats_buffer_data_try_vfmt_inplace(data, len, cap, fmt, ap);
  if (try_rc == 0)
    return 0;
  if (try_rc < 0)
    return -1;

  va_copy(aq, ap);
  n = vsnprintf(NULL, 0, fmt, aq);
  va_end(aq);
  if (n < 0)
    return -1;

  add = (size_t) n;
  return stats_buffer_data_append_vfmt_grow(data, len, cap, fmt, ap, add);
}

int stats_buffer_data_append_fmt(char **data, size_t *len, size_t *cap,
                                 const char *fmt, ...)
{
  va_list ap;
  int r;

  va_start(ap, fmt);
  r = stats_buffer_data_append_vfmt(data, len, cap, fmt, ap);
  va_end(ap);
  return r;
}
