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

size_t stats_format_schema_entry_suffix(char *buf, size_t cap,
                                        struct schema_entry *se);

void stats_format_emit_property_banner(stats_format_emit_fn emit, void *opaque,
                                       int prop_char, const char *prog,
                                       const char *vers, const char *nodename,
                                       const char *sysname, const char *machine,
                                       const char *release, const char *version,
                                       unsigned long long uptime);

void stats_format_emit_schema_line(stats_format_emit_fn emit, void *opaque,
                                   int schema_char, struct stats_type *type);

void stats_format_emit_mark_multiline(stats_format_emit_fn emit, void *opaque,
                                      int mark_char, const char *payload);

/* Append formatted text to *markp (newline between prior and new); frees/replaces *markp. */
int stats_format_append_mark_va(char **markp, const char *fmt, va_list ap);

int stats_format_snprintf_stats_row(char *buf, size_t cap,
                                    struct stats_type *type,
                                    struct stats *stats);

/* Tier-aware row formatter. STATS_ROW_LEGACY matches the legacy row exactly. */
int stats_format_snprintf_stats_row_tier(char *buf, size_t cap,
                                         struct stats_type *type,
                                         struct stats *stats,
                                         enum stats_row_tier tier);

void stats_format_fprint_stats_row(FILE *f, struct stats_type *type,
                                   struct stats *stats);

#endif
