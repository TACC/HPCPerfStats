/* Stats type registry lookup, per-device instances, and metric assignment. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
  return (unsigned long long) ts.tv_sec * 1000000000ULL
         + (unsigned long long) ts.tv_nsec;
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
  stats_destroy(key_to_stats((const char *) key));
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

struct stats_type *stats_type_get(const char *name)
{
  size_t begin = 0;
  size_t end = stats_type_nr;

  if (name == NULL)
    return NULL;

  while (begin < end) {
    size_t mid = begin + (end - begin) / 2;
    struct stats_type *type = stats_type_table[mid];
    int cmp = strcmp(name, type->st_name);

    if (cmp < 0)
      end = mid;
    else if (cmp > 0)
      begin = mid + 1;
    else
      return type;
  }

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
  return (n >= 0 && (size_t) n <= dev_len) ? 0 : -1;
}

static struct stats *stats_create(struct stats_type *type, const char *dev)
{
  struct stats *stats = NULL;
  unsigned long long *val = NULL;
  unsigned char *present = NULL;
  size_t dev_len;

  if (type == NULL || dev == NULL)
    return NULL;

  dev_len = strlen(dev);
  stats = (struct stats *) malloc(sizeof(*stats) + dev_len + 1);
  if (stats == NULL)
    goto err;

  val = (unsigned long long *) calloc(type->st_schema.sc_len, sizeof(*stats->s_val));
  if (val == NULL && type->st_schema.sc_len != 0)
    goto err;

  present = (unsigned char *) calloc(type->st_schema.sc_len, sizeof(*present));
  if (present == NULL && type->st_schema.sc_len != 0)
    goto err;

  memset(stats, 0, sizeof(*stats));
  stats->s_type = type;
  stats->s_val = val;
  stats->s_val_present = present;
  if (stats_copy_device_name(stats, dev) < 0)
    goto err;

  return stats;

 err:
  free(stats);
  free(val);
  free(present);
  return NULL;
}

static void stats_destroy(struct stats *stats)
{
  if (stats == NULL)
    return;
  free(stats->s_val);
  free(stats->s_val_present);
  free(stats);
}

void stats_clear_present(struct stats *stats)
{
  if (stats == NULL || stats->s_val_present == NULL)
    return;
  memset(stats->s_val_present, 0,
         stats->s_type->st_schema.sc_len * sizeof(*stats->s_val_present));
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  struct stats *stats = NULL;
  struct dict_entry *de;
  hash_t hash;

  if (type == NULL)
    return NULL;

  if (dev == NULL)
    dev = "-";

  TRACE("get_current_stats %s %s\n", type->st_name, dev);

  hash = dict_strhash(dev);
  de = dict_entry_ref(&type->st_current_dict, hash, dev);
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

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
#ifdef MONITOR_METRIC_PROFILER
  unsigned long long wall_begin_ns = stats_now_ns(CLOCK_MONOTONIC);
  unsigned long long cpu_begin_ns = stats_now_ns(CLOCK_THREAD_CPUTIME_ID);
#endif
  int i;

  if (stats == NULL || key == NULL)
    return;

  i = schema_ref(&stats->s_type->st_schema, key);

  TRACE("%s %s %s %llu %d\n",
        stats->s_type->st_name, stats->s_dev, key,
        (unsigned long long) val, i);

  if (i >= 0) {
    if (!collect_tier_key_active(stats->s_type, i))
      return;
    stats->s_val[i] = val;
    if (stats->s_val_present != NULL)
      stats->s_val_present[i] = 1;
  }
#ifdef MONITOR_METRIC_PROFILER
  stats_record_metric_profile(stats, key, wall_begin_ns, cpu_begin_ns);
#endif
}

void stats_inc(struct stats *stats, const char *key, unsigned long long val)
{
#ifdef MONITOR_METRIC_PROFILER
  unsigned long long wall_begin_ns = stats_now_ns(CLOCK_MONOTONIC);
  unsigned long long cpu_begin_ns = stats_now_ns(CLOCK_THREAD_CPUTIME_ID);
#endif
  int i;

  if (stats == NULL || key == NULL)
    return;

  i = schema_ref(&stats->s_type->st_schema, key);

  TRACE("%s %s %s %llu %d\n",
        stats->s_type->st_name, stats->s_dev, key,
        (unsigned long long) val, i);

  if (i >= 0) {
    if (!collect_tier_key_active(stats->s_type, i))
      return;
    stats->s_val[i] += val;
    if (stats->s_val_present != NULL)
      stats->s_val_present[i] = 1;
  }
#ifdef MONITOR_METRIC_PROFILER
  stats_record_metric_profile(stats, key, wall_begin_ns, cpu_begin_ns);
#endif
}
