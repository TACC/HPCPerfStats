/* DEBUG-only sentinel provenance, call-site registry, /dev/shm "sentinels" file and
 * fault-injection table (see stats_sentinel_debug.h). */
#ifdef DEBUG

#include "stats_sentinel_debug.h"

#include <errno.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "collect_tier.h"
#include "monitor_log.h"
#include "stats.h"
#include "stats_buffer_debug_shm.h"

#define STATS_FAULT_MAX 16
#define STATS_FAULT_POINT_MAX 32
#define STATS_FAULT_SUBJECT_MAX 128
#define STATS_FAULT_SPEC_MAX (STATS_FAULT_POINT_MAX + STATS_FAULT_SUBJECT_MAX + 32)

extern struct stats_sentinel_site *__start_stats_sentinel_sites[] __attribute__((weak));
extern struct stats_sentinel_site *__stop_stats_sentinel_sites[] __attribute__((weak));

struct stats_fault {
  char spec[STATS_FAULT_SPEC_MAX]; /* the entry as configured, for the fault_fired file */
  char point[STATS_FAULT_POINT_MAX];
  char subject[STATS_FAULT_SUBJECT_MAX];
  int err;
  int delay; /* collect cycles before the fault arms */
  int ticks; /* collect cycles left once armed; -1 until cleared */
  unsigned long fired;
};

static struct stats_fault g_faults[STATS_FAULT_MAX];
static int g_nfaults;

static const char *stats_sentinel_site_base(const struct stats_sentinel_site *site)
{
  const char *slash = strrchr(site->file, '/');

  return slash != NULL ? slash + 1 : site->file;
}

void stats_sentinel_debug_note(const struct stats_sentinel_site *site, struct stats *stats,
                               const char *key, int err)
{
  int i;

  if (stats == NULL || stats->s_val_prov == NULL || key == NULL)
    return;
  i = schema_ref(&stats->s_type->st_schema, key);
  if (i < 0 || !collect_tier_key_active(stats->s_type, i))
    return;
  stats->s_val_prov[i].site = site;
  stats->s_val_prov[i].err = err;
  stats->s_val_prov[i].noted = 1;
}

/* Mirrors stats_set_missing_all: only the active keys still NOT_UPDATED get the code. */
void stats_sentinel_debug_note_all(const struct stats_sentinel_site *site, struct stats *stats,
                                   int err)
{
  if (stats == NULL || stats->s_val_prov == NULL)
    return;
  for (size_t k = 0; k < stats->s_type->st_schema.sc_len; k++) {
    if (stats->s_val_state[k] != STATS_SENTINEL_NOT_UPDATED ||
        !collect_tier_key_active(stats->s_type, (int)k))
      continue;
    stats->s_val_prov[k].site = site;
    stats->s_val_prov[k].err = err;
    stats->s_val_prov[k].noted = 1;
  }
}

void stats_sentinel_debug_note_type(const struct stats_sentinel_site *site, struct stats_type *type,
                                    int err)
{
  size_t j = 0;
  char *dev;

  if (type == NULL)
    return;
  while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL)
    stats_sentinel_debug_note_all(site, key_to_stats(dev), err);
}

static const char *stats_sentinel_debug_state_name(unsigned int code)
{
  return code == STATS_SENTINEL_OK ? "OK" : stats_sentinel_slug(code);
}

static void stats_sentinel_debug_key_cycle(struct stats *stats, size_t k, time_t now)
{
  struct stats_sentinel_prov *p = &stats->s_val_prov[k];
  unsigned char cur = stats->s_val_state[k];

  if (!p->noted) {
    p->site = NULL;
    p->err = 0;
  }
  p->noted = 0;
  if (cur != p->prev) {
    monitor_log_debug("sentinel: %s %s %s %s -> %s (%s:%d errno=%d)\n", stats->s_type->st_name,
                      stats->s_dev, stats->s_type->st_schema.sc_ent[k]->se_key,
                      stats_sentinel_debug_state_name(p->prev),
                      stats_sentinel_debug_state_name(cur),
                      p->site != NULL ? stats_sentinel_site_base(p->site) : "-",
                      p->site != NULL ? p->site->line : 0, p->err);
    p->first = now;
    p->count = 0;
  }
  if (cur != STATS_SENTINEL_OK) {
    p->last = now;
    p->count++;
  }
  p->prev = cur;
}

void stats_sentinel_debug_cycle_end(struct stats_type *type, time_t now)
{
  size_t j = 0;
  char *dev;

  if (type == NULL)
    return;
  while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL) {
    struct stats *stats = key_to_stats(dev);

    if (stats->s_val_prov == NULL)
      continue;
    for (size_t k = 0; k < type->st_schema.sc_len; k++)
      if (collect_tier_key_active(type, (int)k))
        stats_sentinel_debug_key_cycle(stats, k, now);
  }
}

/* Append to a growable buffer; returns -1 on allocation failure. */
static int stats_sentinel_debug_append(char **buf, size_t *len, size_t *cap, const char *fmt, ...)
{
  for (;;) {
    va_list ap;
    int n;

    va_start(ap, fmt);
    n = vsnprintf(*buf + *len, *cap - *len, fmt, ap);
    va_end(ap);
    if (n < 0)
      return -1;
    if ((size_t)n < *cap - *len) {
      *len += (size_t)n;
      return 0;
    }
    char *grown = realloc(*buf, *cap * 2 + (size_t)n);

    if (grown == NULL)
      return -1;
    *buf = grown;
    *cap = *cap * 2 + (size_t)n;
  }
}

static int stats_sentinel_debug_render_row(char **buf, size_t *len, size_t *cap,
                                           const struct stats *stats)
{
  const struct stats_type *type = stats->s_type;

  for (size_t k = 0; k < type->st_schema.sc_len; k++) {
    const struct stats_sentinel_prov *p = &stats->s_val_prov[k];
    unsigned int code = stats->s_val_state[k];
    char site[160];

    if (code == STATS_SENTINEL_OK)
      continue;
    if (p->site != NULL)
      snprintf(site, sizeof(site), "%s:%d", stats_sentinel_site_base(p->site), p->site->line);
    else
      snprintf(site, sizeof(site), "-");
    if (stats_sentinel_debug_append(buf, len, cap, "%s %s %s %u %s %d %s %lld %lld %lu\n",
                                    type->st_name, stats->s_dev, type->st_schema.sc_ent[k]->se_key,
                                    code, stats_sentinel_slug(code), p->err, site,
                                    (long long)p->first, (long long)p->last, p->count) < 0)
      return -1;
  }
  return 0;
}

char *stats_sentinel_debug_render(size_t *len_out)
{
  size_t cap = 4096;
  size_t len = 0;
  char *buf = malloc(cap);
  size_t i = 0;
  struct stats_type *type;

  if (buf == NULL)
    return NULL;
  buf[0] = '\0';
  while ((type = stats_type_for_each(&i)) != NULL) {
    size_t j = 0;
    char *dev;

    if (!type->st_enabled)
      continue;
    while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL) {
      const struct stats *stats = key_to_stats(dev);

      if (stats->s_val_prov != NULL &&
          stats_sentinel_debug_render_row(&buf, &len, &cap, stats) < 0) {
        free(buf);
        return NULL;
      }
    }
  }
  if (len_out != NULL)
    *len_out = len;
  return buf;
}

size_t stats_sentinel_sites_report(FILE *out)
{
  size_t never = 0;

  if (__start_stats_sentinel_sites == NULL)
    return 0;
  for (struct stats_sentinel_site **s = __start_stats_sentinel_sites;
       s < __stop_stats_sentinel_sites; s++) {
    if ((*s)->hits == 0)
      never++;
    if (out != NULL)
      fprintf(out, "%s:%d %lu\n", stats_sentinel_site_base(*s), (*s)->line, (*s)->hits);
  }
  return never;
}

size_t stats_fault_inject_report(FILE *out)
{
  for (int i = 0; i < g_nfaults; i++)
    fprintf(out, "%s %lu\n", g_faults[i].spec, g_faults[i].fired);
  return (size_t)g_nfaults;
}

/* Render `report` into memory and write it as the shm file `name`. */
static void stats_sentinel_debug_write_report(const char *name, size_t (*report)(FILE *))
{
  char *text = NULL;
  size_t len = 0;
  FILE *mem = open_memstream(&text, &len);

  if (mem == NULL)
    return;
  (void)report(mem);
  if (fclose(mem) == 0)
    stats_buffer_debug_shm_write_file(name, text, len);
  free(text);
}

void stats_sentinel_debug_write_shm(void)
{
  size_t len = 0;
  char *buf = stats_sentinel_debug_render(&len);

  if (buf != NULL)
    stats_buffer_debug_shm_write_file("sentinels", buf, len);
  free(buf);
  stats_sentinel_debug_write_report("sentinel_sites", stats_sentinel_sites_report);
  stats_sentinel_debug_write_report("fault_fired", stats_fault_inject_report);
}

/* One "point[:subject][!errno][@[delay+]ticks]" entry; returns -1 when malformed. */
static int stats_fault_parse_entry(const char *entry, size_t n, struct stats_fault *f)
{
  char tmp[STATS_FAULT_SPEC_MAX];
  char *at;
  char *plus;
  char *bang;
  char *colon;
  char *end;

  if (n == 0 || n >= sizeof(tmp))
    return -1;
  memcpy(tmp, entry, n);
  tmp[n] = '\0';
  memset(f, 0, sizeof(*f));
  memcpy(f->spec, tmp, n + 1);
  f->err = EIO;
  f->ticks = -1;
  if ((at = strrchr(tmp, '@')) != NULL) {
    *at = '\0';
    if ((plus = strchr(at + 1, '+')) != NULL) {
      f->delay = (int)strtol(at + 1, &end, 10);
      if (end == at + 1 || end != plus || f->delay < 0)
        return -1;
      at = plus;
    }
    f->ticks = (int)strtol(at + 1, &end, 10);
    if (end == at + 1 || *end != '\0' || f->ticks <= 0)
      return -1;
  }
  if ((bang = strrchr(tmp, '!')) != NULL) {
    *bang = '\0';
    f->err = (int)strtol(bang + 1, &end, 10);
    if (end == bang + 1 || *end != '\0' || f->err <= 0)
      return -1;
  }
  if ((colon = strchr(tmp, ':')) != NULL) {
    *colon = '\0';
    if (snprintf(f->subject, sizeof(f->subject), "%s", colon + 1) >= (int)sizeof(f->subject))
      return -1;
  }
  if (tmp[0] == '\0' || snprintf(f->point, sizeof(f->point), "%s", tmp) >= (int)sizeof(f->point))
    return -1;
  return 0;
}

int stats_fault_inject_set(const char *spec)
{
  const char *p = spec;

  g_nfaults = 0;
  while (p != NULL && *p != '\0') {
    size_t n = strcspn(p, ",");

    if (g_nfaults == STATS_FAULT_MAX || stats_fault_parse_entry(p, n, &g_faults[g_nfaults]) < 0) {
      g_nfaults = 0;
      return -1;
    }
    g_nfaults++;
    p += n;
    if (*p == ',')
      p++;
  }
  return g_nfaults;
}

unsigned long stats_fault_inject_fired(int i)
{
  return i >= 0 && i < g_nfaults ? g_faults[i].fired : 0;
}

int stats_fault_forced(const char *point, const char *subject, int *err)
{
  for (int i = 0; i < g_nfaults; i++) {
    struct stats_fault *f = &g_faults[i];

    if (f->ticks == 0 || f->delay > 0 || strcmp(f->point, point) != 0 ||
        (f->subject[0] != '\0' && (subject == NULL || strstr(subject, f->subject) == NULL)))
      continue;
    f->fired++;
    if (err != NULL)
      *err = f->err;
    return 1;
  }
  return 0;
}

void stats_fault_inject_tick(void)
{
  for (int i = 0; i < g_nfaults; i++)
    if (g_faults[i].delay > 0)
      g_faults[i].delay--;
    else if (g_faults[i].ticks > 0)
      g_faults[i].ticks--;
}

#endif /* DEBUG */
