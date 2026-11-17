/* Stats type registry lookup, per-device instances, and metric assignment. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "collect_tier.h"
#include "dict.h"
#include "metric_profiler.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "trace.h"

int stats_collect_on_changeover = 0;

static void stats_destroy(struct stats *stats);

#ifdef MONITOR_METRIC_PROFILER
static unsigned long long stats_now_ns(clockid_t cid)
{
  struct timespec ts;

  if (clock_gettime(cid, &ts) != 0)
    return 0;
  return (unsigned long long)ts.tv_sec * 1000000000ULL + (unsigned long long)ts.tv_nsec;
}

static void stats_record_metric_profile(struct stats *stats, const char *key,
                                        unsigned long long wall_begin_ns,
                                        unsigned long long cpu_begin_ns)
{
  metric_profiler_record_metric(stats->s_type->st_name, stats->s_dev, key,
                                stats_now_ns(CLOCK_MONOTONIC) - wall_begin_ns,
                                stats_now_ns(CLOCK_THREAD_CPUTIME_ID) - cpu_begin_ns);
}
#endif

int stats_type_init(struct stats_type *type)
{
  if (type == NULL)
    return -1;

  TRACE("type %s, schema_def `%s'\n", type->st_name, type->st_schema_def);

  if (type->st_schema_def == NULL)
    return -1;

  if (schema_init(&type->st_schema, type->st_schema_def) < 0)
    return -1;

  if (dict_init(&type->st_current_dict, 0) < 0)
    return -1;

  return 0;
}

void key_stats_destroy(void *key)
{
  if (key == NULL)
    return;
  stats_destroy(key_to_stats((const char *)key));
}

void stats_type_destroy(struct stats_type *type)
{
  if (type == NULL)
    return;

  if (type->st_schema_def_owned && type->st_schema_def != NULL) {
    free(type->st_schema_def);
    type->st_schema_def = NULL;
    type->st_schema_def_owned = 0;
  }

  schema_destroy(&type->st_schema);
  dict_destroy(&type->st_current_dict, &key_stats_destroy);
}

/* Linear scan: stats_registry.c is ordered by legacy variable names, not st_name, so a
 * binary search missed renamed types (lustre_*, host_mem, nvidia_gpu, host_opa, host_proc). */
struct stats_type *stats_type_get(const char *name)
{
  size_t i = 0;
  struct stats_type *type;

  while (name != NULL && (type = stats_type_for_each(&i)) != NULL)
    if (strcmp(name, type->st_name) == 0)
      return type;
  return NULL;
}

struct stats_type *stats_type_for_each(size_t *i)
{
  if (i == NULL)
    return NULL;

  if (*i < stats_type_nr) {
    struct stats_type *type = stats_type_table[*i];

    (*i)++;
    return type;
  }

  return NULL;
}

static int stats_copy_device_name(struct stats *stats, const char *dev)
{
  size_t dev_len = strlen(dev);
  int n;

  n = snprintf(stats->s_dev, dev_len + 1, "%s", dev);
  return (n >= 0 && (size_t)n <= dev_len) ? 0 : -1;
}

static struct stats *stats_create(struct stats_type *type, const char *dev)
{
  struct stats *stats = NULL;
  unsigned long long *val = NULL;
  unsigned char *state = NULL;
#ifdef DEBUG
  struct stats_sentinel_prov *prov = NULL;
#endif
  size_t dev_len;

  if (type == NULL || dev == NULL)
    return NULL;

  dev_len = strlen(dev);
  stats = (struct stats *)malloc(sizeof(*stats) + dev_len + 1);
  if (stats == NULL)
    goto err;

  val = (unsigned long long *)calloc(type->st_schema.sc_len, sizeof(*stats->s_val));
  if (val == NULL && type->st_schema.sc_len != 0)
    goto err;

  /* A key no collector has set yet must not print the calloc 0 as a reading. */
  state = (unsigned char *)malloc(type->st_schema.sc_len);
  if (state == NULL && type->st_schema.sc_len != 0)
    goto err;
  if (state != NULL)
    memset(state, STATS_SENTINEL_NOT_UPDATED, type->st_schema.sc_len);
#ifdef DEBUG
  prov = (struct stats_sentinel_prov *)calloc(type->st_schema.sc_len + 1, sizeof(*prov));
  if (prov == NULL)
    goto err;
#endif

  memset(stats, 0, sizeof(*stats));
  stats->s_type = type;
  stats->s_val = val;
  stats->s_val_state = state;
#ifdef DEBUG
  stats->s_val_prov = prov;
#endif
  if (stats_copy_device_name(stats, dev) < 0)
    goto err;

  return stats;

err:
#ifdef DEBUG
  free(prov);
#endif
  free(stats);
  free(val);
  free(state);
  return NULL;
}

static void stats_destroy(struct stats *stats)
{
  if (stats == NULL)
    return;
  free(stats->s_val);
  free(stats->s_val_state);
#ifdef DEBUG
  free(stats->s_val_prov);
#endif
  free(stats);
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  struct stats *stats = NULL;
  struct dict_entry *de;
  hash_t hash;
  static time_t last_dict_warn;

  if (type == NULL)
    return NULL;

  if (dev == NULL)
    dev = "-";

  TRACE("get_current_stats %s %s\n", type->st_name, dev);

  hash = dict_strhash(dev);
  de = dict_entry_ref(&type->st_current_dict, hash, dev);
  if (de == NULL) {
    time_t now = time(NULL);

    if (last_dict_warn == 0 || (now > last_dict_warn && now - last_dict_warn >= 60)) {
      ERROR("get_current_stats: dict_entry_ref failed for %s %s (corrupt/empty dict); "
            "skipping device this tick\n",
            type->st_name != NULL ? type->st_name : "?", dev);
      last_dict_warn = now;
    }
    return NULL;
  }
  if (de->d_key != NULL)
    return key_to_stats(de->d_key);

  stats = stats_create(type, dev);
  if (stats == NULL) {
    ERROR("stats_create: %m\n");
    return NULL;
  }

  if (dict_entry_set(&type->st_current_dict, de, hash, stats->s_dev) < 0) {
    ERROR("dict_entry_set: %m\n");
    stats_destroy(stats);
    return NULL;
  }

  return stats;
}

/* Schema index of `key` when it may be stored in the current collect phase, else -1. */
static int stats_active_slot(struct stats *stats, const char *key)
{
  int i;

  if (stats == NULL || key == NULL)
    return -1;
  i = schema_ref(&stats->s_type->st_schema, key);
  TRACE("%s %s %s %d\n", stats->s_type->st_name, stats->s_dev, key, i);
  return (i >= 0 && collect_tier_key_active(stats->s_type, i)) ? i : -1;
}

static void stats_store(struct stats *stats, const char *key, unsigned long long val, int add)
{
#ifdef MONITOR_METRIC_PROFILER
  unsigned long long wall_begin_ns = stats_now_ns(CLOCK_MONOTONIC);
  unsigned long long cpu_begin_ns = stats_now_ns(CLOCK_THREAD_CPUTIME_ID);
#endif
  int i = stats_active_slot(stats, key);

  if (i < 0)
    return;
  stats->s_val[i] = add ? stats->s_val[i] + val : val;
  stats->s_val_state[i] = STATS_SENTINEL_OK;
#ifdef MONITOR_METRIC_PROFILER
  stats_record_metric_profile(stats, key, wall_begin_ns, cpu_begin_ns);
#endif
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  stats_store(stats, key, val, 0);
}

void stats_inc(struct stats *stats, const char *key, unsigned long long val)
{
  stats_store(stats, key, val, 1);
}

void stats_accumulate(struct stats *stats, const char *key, unsigned long long val)
{
  int i = stats_active_slot(stats, key);

  if (i >= 0 && stats->s_val_state[i] <= STATS_SENTINEL_NOT_UPDATED)
    stats_store(stats, key, val, stats->s_val_state[i] == STATS_SENTINEL_OK);
}

void(stats_set_missing)(struct stats *stats, const char *key, enum stats_sentinel code)
{
  int i = stats_active_slot(stats, key);

  if (i >= 0)
    stats->s_val_state[i] = (unsigned char)code;
}

/* Set `code` on the keys active in the current phase; with `only_unset`, only on those still
 * STATS_SENTINEL_NOT_UPDATED this cycle. */
static void stats_mark_active_keys(struct stats *stats, enum stats_sentinel code, int only_unset)
{
  for (size_t k = 0; k < stats->s_type->st_schema.sc_len; k++)
    if ((!only_unset || stats->s_val_state[k] == STATS_SENTINEL_NOT_UPDATED) &&
        collect_tier_key_active(stats->s_type, (int)k))
      stats->s_val_state[k] = (unsigned char)code;
}

void(stats_set_missing_all)(struct stats *stats, enum stats_sentinel code)
{
  if (stats != NULL)
    stats_mark_active_keys(stats, code, 1);
}

static void stats_type_mark_rows(struct stats_type *type, enum stats_sentinel code, int only_unset)
{
  size_t j = 0;
  char *dev;

  if (type == NULL)
    return;
  while ((dev = dict_for_each(&type->st_current_dict, &j)) != NULL)
    stats_mark_active_keys(key_to_stats(dev), code, only_unset);
}

void(stats_type_set_missing_all)(struct stats_type *type, enum stats_sentinel code)
{
  stats_type_mark_rows(type, code, 1);
}

void stats_type_mark_not_updated(struct stats_type *type)
{
  stats_type_mark_rows(type, STATS_SENTINEL_NOT_UPDATED, 0);
}

/* 1 when the current phase has an active key of `stats` and none was touched this cycle. */
static int stats_untouched(struct stats *stats)
{
  int active = 0;

  for (size_t k = 0; k < stats->s_type->st_schema.sc_len; k++) {
    if (!collect_tier_key_active(stats->s_type, (int)k))
      continue;
    if (stats->s_val_state[k] != STATS_SENTINEL_NOT_UPDATED)
      return 0;
    active = 1;
  }
  return active;
}

void stats_type_prune_untouched(struct stats_type *type)
{
  size_t j = 0;
  struct dict_entry *de;

  if (type == NULL || !type->st_prune_untouched)
    return;
  /* Removal leaves a dummy slot, so the scan index stays valid. */
  while ((de = dict_for_each_ref(&type->st_current_dict, &j)) != NULL)
    if (stats_untouched(key_to_stats(de->d_key)))
      stats_destroy(key_to_stats(dict_entry_remv(&type->st_current_dict, de, 1)));
}
