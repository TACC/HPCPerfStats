/* Batched stats row assembly into RMQ payload buffers. */
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>

#include "collect.h"
#include "collect_tier.h"
#include "monitor_release_log.h"
#include "stats.h"
#include "stats_buffer.h"
#include "stats_buffer_data_append.h"
#include "stats_buffer_rows.h"
#include "stats_text_format.h"

static char *row_line_buf;
static size_t row_line_cap;
static stats_buffer_row_filter_fn g_row_filter;
static void *g_row_filter_ctx;

void stats_buffer_set_row_filter(stats_buffer_row_filter_fn fn, void *ctx)
{
  g_row_filter = fn;
  g_row_filter_ctx = ctx;
}

static void stats_buffer_append_fmt(struct stats_buffer *sf, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));

static void stats_buffer_append_fmt(struct stats_buffer *sf, const char *fmt, ...)
{
  va_list ap;
  va_start(ap, fmt);
  if (stats_buffer_data_append_vfmt(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap, fmt, ap) <
      0) {
    /* Best-effort on OOM (buffer unchanged). */
  }
  va_end(ap);
}

static int stats_buffer_append_type_row(struct stats_buffer *sf, struct stats_type *type,
                                        struct stats *stats, enum stats_row_tier tier)
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
      int total =
          stats_format_snprintf_stats_row_tier(row_line_buf, row_line_cap, type, stats, tier);

      if (total < 0)
        return -1;
      if ((size_t)total >= row_line_cap)
        continue;
      row_line_buf[total] = '\n';
      return stats_buffer_data_append_bytes(&sf->sf_data, &sf->sf_data_len, &sf->sf_data_cap,
                                            row_line_buf, (size_t)(total + 1));
    }
  }
  return -1;
}

static void stats_buffer_append_type_row_fallback(struct stats_buffer *sf, struct stats_type *type,
                                                  struct stats *stats, enum stats_row_tier tier)
{
  const char *tok = (tier == STATS_ROW_FAST) ? " @fast" : (tier == STATS_ROW_FULL) ? " @full" : "";

  stats_buffer_append_fmt(sf, "%s %s%s", type->st_name, stats->s_dev, tok);
  for (size_t k = 0; k < type->st_schema.sc_len; k++) {
    if (tier == STATS_ROW_FAST && type->st_schema.sc_ent[k]->se_collect_tier != COLLECT_TIER_FAST)
      continue;
    unsigned int code = stats_format_value_sentinel(stats, k);

    if (code != 0)
      stats_buffer_append_fmt(sf, " -%u", code);
    else
      stats_buffer_append_fmt(sf, " %llu", stats->s_val[k]);
  }
  stats_buffer_append_fmt(sf, "\n");
}

/* Per-code count of the sentinel tokens a row emitted, for the release hourly log. */
static void stats_buffer_note_row_sentinels(const struct stats_type *type,
                                            const struct stats *stats, enum stats_row_tier tier)
{
  for (size_t k = 0; k < type->st_schema.sc_len; k++) {
    unsigned int code = stats_format_value_sentinel(stats, k);

    if (code != 0 &&
        (tier != STATS_ROW_FAST || type->st_schema.sc_ent[k]->se_collect_tier == COLLECT_TIER_FAST))
      monitor_release_sentinel_note(code);
  }
}

enum stats_row_tier stats_buffer_row_tier_decide(int is_schema_payload, int tier_enabled, int phase)
{
  if (!tier_enabled)
    return STATS_ROW_LEGACY;
  if (is_schema_payload || phase == COLLECT_FULL)
    return STATS_ROW_FULL;
  return STATS_ROW_FAST;
}

void stats_buffer_append_enabled_type_rows(struct stats_buffer *sf, enum stats_row_tier tier)
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

      if (g_row_filter != NULL && !g_row_filter(type->st_name, stats->s_dev, g_row_filter_ctx))
        continue;
      if (stats_buffer_append_type_row(sf, type, stats, tier) < 0)
        stats_buffer_append_type_row_fallback(sf, type, stats, tier);
      stats_buffer_note_row_sentinels(type, stats, tier);
    }
  }
}
