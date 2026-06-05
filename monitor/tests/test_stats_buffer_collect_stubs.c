/*
 * Minimal stubs for test_stats_buffer_collect / tier-row ring_buffer tests.
 */
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "dict.h"
#include "schema.h"
#include "stats.h"

struct stats_buffer_collect_fixture {
  struct stats_type type;
  struct stats *stats;
};

struct stats_buffer_collect_fixture *g_stats_buffer_collect_fixture;

void cpu_stats_invalidate_file_caches(void) {}
void net_stats_invalidate_iface_cache(void) {}

int pscanf(const char *path, const char *fmt, ...)
{
  (void)path;
  (void)fmt;
  return 0;
}

struct stats_type *stats_type_for_each(size_t *i)
{
  struct stats_buffer_collect_fixture *fx = g_stats_buffer_collect_fixture;

  if (fx == NULL || *i != 0)
    return NULL;
  (*i)++;
  return &fx->type;
}

int stats_buffer_collect_fixture_init(struct stats_buffer_collect_fixture *fx,
				      const char *schema_def,
				      const unsigned long long *vals, size_t nvals)
{
  size_t k;
  size_t n;

  if (fx == NULL || schema_def == NULL)
    return -1;
  memset(fx, 0, sizeof(*fx));
  snprintf(fx->type.st_name, sizeof(fx->type.st_name), "%s", "host_tt");
  if (schema_init(&fx->type.st_schema, schema_def) < 0)
    return -1;
  fx->type.st_enabled = 1;
  if (dict_init(&fx->type.st_current_dict, 4) < 0)
    return -1;

  n = fx->type.st_schema.sc_len;
  fx->stats = malloc(sizeof(*fx->stats) + 8);
  if (fx->stats == NULL)
    return -1;
  fx->stats->s_type = &fx->type;
  fx->stats->s_val = calloc(n, sizeof(*fx->stats->s_val));
  fx->stats->s_val_present = calloc(n, 1);
  if (fx->stats->s_val == NULL || fx->stats->s_val_present == NULL)
    return -1;
  for (k = 0; k < n; k++) {
    fx->stats->s_val[k] = (k < nvals) ? vals[k] : 0ULL;
    fx->stats->s_val_present[k] = 1;
  }
  strcpy(fx->stats->s_dev, "dev0");
  if (dict_set(&fx->type.st_current_dict, fx->stats->s_dev) < 0)
    return -1;
  g_stats_buffer_collect_fixture = fx;
  return 0;
}

void stats_buffer_collect_fixture_teardown(struct stats_buffer_collect_fixture *fx)
{
  if (fx == NULL)
    return;
  g_stats_buffer_collect_fixture = NULL;
  schema_destroy(&fx->type.st_schema);
  dict_destroy(&fx->type.st_current_dict, NULL);
  free(fx->stats->s_val);
  free(fx->stats->s_val_present);
  free(fx->stats);
  fx->stats = NULL;
  memset(fx, 0, sizeof(*fx));
}
