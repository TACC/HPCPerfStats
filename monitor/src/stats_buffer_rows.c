/* Batched stats row assembly into RMQ payload buffers. */
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

#include "collect.h"
#include "stats.h"
#include "stats_buffer.h"
#include "stats_buffer_data_append.h"
#include "stats_buffer_rows.h"
#include "stats_text_format.h"

static char *row_line_buf;
static size_t row_line_cap;

static void stats_buffer_append_fmt(struct stats_buffer *sf, const char *fmt, ...)
  __attribute__((format(printf, 2, 3)));

static void stats_buffer_append_fmt(struct stats_buffer *sf, const char *fmt, ...)
{
  va_list ap;
  va_start(ap, fmt);
  if (stats_buffer_data_append_vfmt(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, fmt, ap) < 0) {
    /* Best-effort on OOM (buffer unchanged). */
  }
  va_end(ap);
}

static int stats_buffer_append_type_row(struct stats_buffer *sf, struct stats_type *type, struct stats *stats)
{
  int attempt;

  for (attempt = 0; attempt < 8; attempt++) {
    size_t need = strlen(type->st_name) + 1 + strlen(stats->s_dev) + 4;
    size_t k;

    for (k = 0; k < type->st_schema.sc_len; k++)
      need += 24;
    if (need < 256)
      need = 256;
    if (need > row_line_cap) {
      char *nr = realloc(row_line_buf, need);

      if (nr == NULL)
	return -1;
      row_line_buf = nr;
      row_line_cap = need;
    }

    {
      int total = stats_format_snprintf_stats_row(row_line_buf, row_line_cap,
						  type, stats);

      if (total < 0)
	return -1;
      if ((size_t)total >= row_line_cap)
	continue;
      row_line_buf[total] = '\n';
      return stats_buffer_data_append_bytes(&sf->sf_data, &sf->sf_data_len,
					    &sf->sf_data_cap, row_line_buf,
					    (size_t)(total + 1));
    }
  }
  return -1;
}

void stats_buffer_append_enabled_type_rows(struct stats_buffer *sf)
{
  size_t i = 0;
  struct stats_type *type;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!(type->st_enabled))
      continue;

    size_t j = 0;
    char *dev;
    while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL) {
      struct stats *stats = key_to_stats(dev);

      if (stats_buffer_append_type_row(sf, type, stats) < 0) {
	stats_buffer_append_fmt(sf, "%s %s", type->st_name, stats->s_dev);
	for (size_t k = 0; k < type->st_schema.sc_len; k++)
	  stats_buffer_append_fmt(sf, " %llu", stats->s_val[k]);
	stats_buffer_append_fmt(sf, "\n");
      }
    }
  }
}

