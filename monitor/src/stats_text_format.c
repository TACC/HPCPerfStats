/* Format schema suffixes, property banners, marks, and whitespace-separated stat rows. */
#include "stats_text_format.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "schema.h"
#include "stats.h"

static void stats_format_append_type_flag(char **p, char *end, int is_control)
{
  if ((size_t) (end - *p) < 3)
    return;
  if (is_control) {
    memcpy(*p, ",C", 3);
    *p += 2;
  } else {
    memcpy(*p, ",E", 3);
    *p += 2;
  }
}

size_t stats_format_schema_entry_suffix(char *buf, size_t cap,
                                        struct schema_entry *se)
{
  char tmp[96];
  char *p = tmp;
  char *end = tmp + sizeof(tmp);

  if (se == NULL)
    return 0;

  *p = '\0';

  if (se->se_type == SE_CONTROL)
    stats_format_append_type_flag(&p, end, 1);
  else if (se->se_type == SE_EVENT)
    stats_format_append_type_flag(&p, end, 0);

  if (se->se_unit != NULL) {
    int n = snprintf(p, (size_t) (end - p), ",U=%s", se->se_unit);

    if (n > 0 && (size_t) n < (size_t) (end - p))
      p += n;
    else
      p = end;
  }
  if (se->se_width != 0) {
    int n = snprintf(p, (size_t) (end - p), ",W=%u", se->se_width);

    if (n > 0 && (size_t) n < (size_t) (end - p))
      p += n;
    else
      p = end;
  }
  if (se->se_collect_tier == COLLECT_TIER_SLOW) {
    int n = snprintf(p, (size_t) (end - p), ",R=S");

    if (n > 0 && (size_t) n < (size_t) (end - p))
      p += n;
    else
      p = end;
  }

  {
    size_t len = (size_t) (p - tmp);

    if (buf != NULL && cap > len)
      memcpy(buf, tmp, len + 1);
    return len;
  }
}

void stats_format_emit_property_banner(stats_format_emit_fn emit, void *opaque,
                                       int prop_char, const char *prog,
                                       const char *vers, const char *nodename,
                                       const char *sysname, const char *machine,
                                       const char *release, const char *version,
                                       unsigned long long uptime)
{
  if (emit == NULL)
    return;

  emit(opaque, "%c%s %s\n", prop_char, prog != NULL ? prog : "",
       vers != NULL ? vers : "");
  emit(opaque, "%chostname %s\n", prop_char, nodename != NULL ? nodename : "");
  emit(opaque, "%cuname %s %s %s %s\n", prop_char,
       sysname != NULL ? sysname : "",
       machine != NULL ? machine : "",
       release != NULL ? release : "",
       version != NULL ? version : "");
  emit(opaque, "%cuptime %llu\n", prop_char,
       (unsigned long long) uptime);
}

static void stats_format_emit_one_schema_entry(stats_format_emit_fn emit,
                                               void *opaque,
                                               struct schema_entry *se)
{
  char suf[96];
  char entry[256];
  int n;

  stats_format_schema_entry_suffix(suf, sizeof(suf), se);
  n = snprintf(entry, sizeof(entry), " %s%s", se->se_key, suf);
  if (n > 0 && (size_t) n < sizeof(entry))
    emit(opaque, "%s", entry);
  else {
    emit(opaque, " %s", se->se_key);
    emit(opaque, "%s", suf);
  }
}

void stats_format_emit_schema_line(stats_format_emit_fn emit, void *opaque,
                                   int schema_char, struct stats_type *type)
{
  size_t j;

  if (emit == NULL || type == NULL)
    return;

  emit(opaque, "%c%s", schema_char, type->st_name);
  for (j = 0; j < type->st_schema.sc_len; j++)
    stats_format_emit_one_schema_entry(emit, opaque, type->st_schema.sc_ent[j]);
  emit(opaque, "\n");
}

void stats_format_emit_mark_multiline(stats_format_emit_fn emit, void *opaque,
                                      int mark_char, const char *payload)
{
  const char *str;

  if (emit == NULL || payload == NULL)
    return;

  str = payload;
  while (*str != '\0') {
    const char *eol = strchr(str, '\n');

    if (eol == NULL)
      eol = str + strlen(str);

    emit(opaque, "%c%.*s\n", mark_char, (int) (eol - str), str);
    str = eol;
    if (*str == '\n')
      str++;
  }
}

int stats_format_append_mark_va(char **markp, const char *fmt, va_list ap)
{
  char *suffix = NULL;
  char *merged = NULL;
  int n;

  if (markp == NULL || fmt == NULL)
    return -1;

  n = vasprintf(&suffix, fmt, ap);
  if (n < 0)
    return -1;

  if (suffix == NULL || suffix[0] == '\0') {
    free(suffix);
    return 0;
  }

  if (*markp == NULL || (*markp)[0] == '\0') {
    free(*markp);
    *markp = suffix;
    return 0;
  }

  if (asprintf(&merged, "%s\n%s", *markp, suffix) < 0) {
    free(suffix);
    return -1;
  }
  free(*markp);
  free(suffix);
  *markp = merged;
  return 0;
}

static int stats_format_row_key_emitted(struct stats_type *type, size_t k,
                                        enum stats_row_tier tier)
{
  /* FAST rows carry only fast-tier keys; LEGACY/FULL carry every key. */
  if (tier != STATS_ROW_FAST)
    return 1;
  return type->st_schema.sc_ent[k]->se_collect_tier == COLLECT_TIER_FAST;
}

static int stats_format_append_values(char *buf, size_t cap, size_t used,
                                      struct stats_type *type,
                                      struct stats *stats,
                                      enum stats_row_tier tier)
{
  size_t k;
  char *p = buf + used;
  size_t rem = (used < cap) ? cap - used : 0;
  int n;

  for (k = 0; k < type->st_schema.sc_len; k++) {
    if (!stats_format_row_key_emitted(type, k, tier))
      continue;
    n = snprintf(p, rem, " %llu",
                 (unsigned long long) stats->s_val[k]);
    if (n < 0)
      return -1;
    if ((size_t) n >= rem)
      return (int) (used + (size_t) n + 64);
    p += n;
    rem -= (size_t) n;
    used += (size_t) n;
  }
  return (int) used;
}

static const char *stats_format_row_tier_token(enum stats_row_tier tier)
{
  switch (tier) {
  case STATS_ROW_FAST:
    return " @fast";
  case STATS_ROW_FULL:
    return " @full";
  case STATS_ROW_LEGACY:
  default:
    return "";
  }
}

int stats_format_snprintf_stats_row_tier(char *buf, size_t cap,
                                         struct stats_type *type,
                                         struct stats *stats,
                                         enum stats_row_tier tier)
{
  int n;

  if (buf == NULL || type == NULL || stats == NULL || cap == 0)
    return -1;

  n = snprintf(buf, cap, "%s %s%s", type->st_name, stats->s_dev,
               stats_format_row_tier_token(tier));
  if (n < 0)
    return -1;
  if ((size_t) n >= cap)
    return n + 64;

  return stats_format_append_values(buf, cap, (size_t) n, type, stats, tier);
}

int stats_format_snprintf_stats_row(char *buf, size_t cap,
                                    struct stats_type *type,
                                    struct stats *stats)
{
  return stats_format_snprintf_stats_row_tier(buf, cap, type, stats,
                                              STATS_ROW_LEGACY);
}

void stats_format_fprint_stats_row(FILE *f, struct stats_type *type,
                                   struct stats *stats)
{
  char stackbuf[4096];
  int n;

  if (f == NULL || type == NULL || stats == NULL)
    return;

  n = stats_format_snprintf_stats_row(stackbuf, sizeof(stackbuf), type, stats);
  if (n >= 0 && (size_t) n < sizeof(stackbuf)) {
    fprintf(f, "%s\n", stackbuf);
    return;
  }

  fprintf(f, "%s %s", type->st_name, stats->s_dev);
  for (size_t k = 0; k < type->st_schema.sc_len; k++)
    fprintf(f, " %llu", (unsigned long long) stats->s_val[k]);
  fprintf(f, "\n");
}
