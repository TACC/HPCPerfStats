#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>

#include "stats_buffer_data_append.h"

int stats_buffer_data_append_vfmt(char **data, size_t *len, size_t *cap, const char *fmt,
				  va_list ap)
{
  va_list aq;
  va_copy(aq, ap);
  int n = vsnprintf(NULL, 0, fmt, aq);
  va_end(aq);
  if (n < 0)
    return -1;

  size_t add = (size_t)n;
  size_t need = *len + add + 1;
  if (need > *cap) {
    size_t ncap = *cap ? *cap : 64;
    while (ncap < need)
      ncap *= 2;
    char *p = realloc(*data, ncap);
    if (p == NULL)
      return -1;
    *data = p;
    *cap = ncap;
  }

  va_copy(aq, ap);
  vsnprintf(*data + *len, *cap - *len, fmt, aq);
  va_end(aq);
  *len += add;
  return 0;
}

int stats_buffer_data_append_fmt(char **data, size_t *len, size_t *cap, const char *fmt, ...)
{
  va_list ap;
  va_start(ap, fmt);
  int r = stats_buffer_data_append_vfmt(data, len, cap, fmt, ap);
  va_end(ap);
  return r;
}
