/* Text formatting for archive/RabbitMQ payloads: schema lines, marks, stat rows. */
#ifndef STATS_TEXT_FORMAT_H
#define STATS_TEXT_FORMAT_H

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>

struct schema_entry;
struct stats_type;
struct stats;

typedef void (*stats_format_emit_fn)(void *opaque, const char *fmt, ...);

/* Sample row tier marker controlling which values a row carries.
 *  - LEGACY: no `@` token, every schema value (backward compatible archives).
 *  - FAST:   "@fast" token, only fast-tier values in schema order.
 *  - FULL:   "@full" token, every value in schema order. */
enum stats_row_tier {
  STATS_ROW_LEGACY = 0,
  STATS_ROW_FAST = 1,
  STATS_ROW_FULL = 2,
};

/* Config `emit_value_sentinels` (default 1). At 0 every row prints s_val
 * unchanged and the banner has no `$sentinels` line. */
void stats_format_set_value_sentinels(int enabled);
int stats_format_value_sentinels(void);

/* Code a row prints as "-<code>" for schema index `k`, or 0 to print s_val[k]. */
unsigned int stats_format_value_sentinel(const struct stats *stats, size_t k);

size_t stats_format_schema_entry_suffix(char *buf, size_t cap, struct schema_entry *se);

void stats_format_emit_property_banner(stats_format_emit_fn emit, void *opaque, int prop_char,
                                       const char *prog, const char *vers, const char *nodename,
                                       const char *sysname, const char *machine,
                                       const char *release, const char *version,
                                       unsigned long long uptime, const char *capability_slug);

void stats_format_emit_schema_line(stats_format_emit_fn emit, void *opaque, int schema_char,
                                   struct stats_type *type);

void stats_format_emit_mark_multiline(stats_format_emit_fn emit, void *opaque, int mark_char,
                                      const char *payload);

/* Append formatted text to *markp (newline between prior and new); frees/replaces *markp. */
int stats_format_append_mark_va(char **markp, const char *fmt, va_list ap);

int stats_format_snprintf_stats_row(char *buf, size_t cap, struct stats_type *type,
                                    struct stats *stats);

/* Tier-aware row formatter. STATS_ROW_LEGACY matches the legacy row exactly. */
int stats_format_snprintf_stats_row_tier(char *buf, size_t cap, struct stats_type *type,
                                         struct stats *stats, enum stats_row_tier tier);

#endif
