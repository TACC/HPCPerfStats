/*
 * Multi-type deterministic fixture for debug shm golden emission tests.
 */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collect_tier.h"
#include "dict.h"
#include "schema.h"
#include "stats.h"

void test_debug_shm_emit_fixture_teardown(void);

#define EMIT_FIXTURE_N_TYPES 6

struct emit_fixture_type {
  struct stats_type type;
  struct stats *stats;
};

static struct emit_fixture_type g_emit_types[EMIT_FIXTURE_N_TYPES];
static int g_emit_fixture_active;

static const struct {
  const char *name;
  const char *schema;
  const char *dev;
} g_emit_type_defs[EMIT_FIXTURE_N_TYPES] = {
  { "emit_extra", "load_1, load_5,", "dev0" },
  { "emit_fast", "cycles,E instr_retired,E", "dev0" },
  { "emit_mixed", "rx_bytes,E tx_bytes,E,R=S link_up,E", "dev0" },
  { "host_mem", "mem_total,U=KB mem_free,U=KB dirty,U=KB", "mem" },
  { "host_net", "rx_bytes,E,U=B tx_bytes,E,U=B collisions,E", "eth0" },
  { "host_ps", "ctxt,E processes,E load_1, load_5,", "-" },
};

void cpu_stats_invalidate_file_caches(void) {}
void net_stats_invalidate_iface_cache(void) {}

int pscanf(const char *path, const char *fmt, ...)
{
  (void) path;
  (void) fmt;
  return 0;
}

struct stats_type *stats_type_for_each(size_t *i)
{
  if (!g_emit_fixture_active || *i >= EMIT_FIXTURE_N_TYPES)
    return NULL;
  return &g_emit_types[(*i)++].type;
}

static void emit_fixture_free_stats(struct stats *stats)
{
  if (stats == NULL)
    return;
  free(stats->s_val);
  free(stats->s_val_present);
  free(stats);
}

static void emit_fixture_free_dict_key(void *key)
{
  emit_fixture_free_stats(key_to_stats((const char *) key));
}

static int emit_fixture_init_one(struct emit_fixture_type *slot,
				 const char *name, const char *schema_def,
				 const char *dev)
{
  size_t k;
  size_t n;

  memset(slot, 0, sizeof(*slot));
  snprintf(slot->type.st_name, sizeof(slot->type.st_name), "%s", name);
  if (schema_init(&slot->type.st_schema, schema_def) < 0)
    return -1;
  slot->type.st_enabled = 1;
  collect_tier_apply_to_type(&slot->type);
  if (dict_init(&slot->type.st_current_dict, 4) < 0)
    return -1;

  n = slot->type.st_schema.sc_len;
  slot->stats = malloc(sizeof(*slot->stats) + 8);
  if (slot->stats == NULL)
    return -1;
  slot->stats->s_type = &slot->type;
  slot->stats->s_val = calloc(n, sizeof(*slot->stats->s_val));
  slot->stats->s_val_present = calloc(n, 1);
  if (slot->stats->s_val == NULL || slot->stats->s_val_present == NULL)
    return -1;
  for (k = 0; k < n; k++) {
    slot->stats->s_val[k] = 10ULL + (unsigned long long) k;
    slot->stats->s_val_present[k] = 1;
  }
  strcpy(slot->stats->s_dev, dev);
  if (dict_set(&slot->type.st_current_dict, slot->stats->s_dev) < 0)
    return -1;
  return 0;
}

int test_debug_shm_emit_fixture_init(void)
{
  size_t i;

  test_debug_shm_emit_fixture_teardown();
  collect_tier_set_enabled(1);
  for (i = 0; i < EMIT_FIXTURE_N_TYPES; i++) {
    if (emit_fixture_init_one(&g_emit_types[i], g_emit_type_defs[i].name,
			      g_emit_type_defs[i].schema,
			      g_emit_type_defs[i].dev) < 0) {
      test_debug_shm_emit_fixture_teardown();
      return -1;
    }
  }
  g_emit_fixture_active = 1;
  return 0;
}

const struct stats_type *test_debug_shm_emit_fixture_type_by_name(const char *name)
{
  size_t i;

  if (!g_emit_fixture_active || name == NULL)
    return NULL;
  for (i = 0; i < EMIT_FIXTURE_N_TYPES; i++) {
    if (strcmp(g_emit_types[i].type.st_name, name) == 0)
      return &g_emit_types[i].type;
  }
  return NULL;
}

void test_debug_shm_emit_fixture_teardown(void)
{
  size_t i;

  g_emit_fixture_active = 0;
  for (i = 0; i < EMIT_FIXTURE_N_TYPES; i++) {
    schema_destroy(&g_emit_types[i].type.st_schema);
    dict_destroy(&g_emit_types[i].type.st_current_dict, emit_fixture_free_dict_key);
    g_emit_types[i].stats = NULL;
    memset(&g_emit_types[i], 0, sizeof(g_emit_types[i]));
  }
}
