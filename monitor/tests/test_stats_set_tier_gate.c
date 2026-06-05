/* stats_set/stats_inc tier gating and str_collect_key_list hook alignment. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "collect.h"
#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"

struct stats_type *const stats_type_table[] = { NULL };
const size_t stats_type_nr = 0;
static struct stats_type g_type;
static unsigned long long g_vals[5];
static unsigned char g_present[5];
static char g_stats_storage[sizeof(struct stats) + 16];
static struct stats *g_stats;

static void setup_host_mem_type(void)
{
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "host_mem");
  assert(schema_init(&g_type.st_schema,
                     "mem_total mem_free active inactive dirty") == 0);
  collect_tier_set_enabled(1);
  collect_tier_apply_to_type(&g_type);
  memset(g_vals, 0, sizeof(g_vals));
  memset(g_present, 0, sizeof(g_present));
  memset(g_stats_storage, 0, sizeof(g_stats_storage));
  g_stats = (struct stats *) g_stats_storage;
  g_stats->s_type = &g_type;
  g_stats->s_val = g_vals;
  g_stats->s_val_present = g_present;
  strcpy(g_stats->s_dev, "-");
}

static void test_stats_set_skips_slow_on_fast_phase(void)
{
  setup_host_mem_type();
  collect_tier_set_phase(COLLECT_FAST_ONLY);

  stats_set(g_stats, "mem_total", 100ULL);
  stats_set(g_stats, "inactive", 50ULL);

  assert(g_vals[0] == 100ULL);
  assert(g_present[0] == 1);
  assert(g_vals[3] == 0ULL);
  assert(g_present[3] == 0);

  collect_tier_set_phase(COLLECT_FULL);
  stats_set(g_stats, "inactive", 50ULL);
  assert(g_vals[3] == 50ULL);
  assert(g_present[3] == 1);

  schema_destroy(&g_type.st_schema);
}

static void test_stats_inc_skips_slow_on_fast_phase(void)
{
  setup_host_mem_type();
  collect_tier_set_phase(COLLECT_FULL);
  stats_set(g_stats, "dirty", 10ULL);

  collect_tier_set_phase(COLLECT_FAST_ONLY);
  stats_inc(g_stats, "dirty", 5ULL);
  assert(g_vals[4] == 10ULL);

  collect_tier_set_phase(COLLECT_FULL);
  stats_inc(g_stats, "dirty", 5ULL);
  assert(g_vals[4] == 15ULL);

  schema_destroy(&g_type.st_schema);
}

static int tier_key_active_hook(void *ctx, struct stats *stats, const char *key)
{
  int idx;

  (void) ctx;
  if (stats == NULL || key == NULL)
    return 1;
  idx = schema_ref(&stats->s_type->st_schema, key);
  if (idx < 0)
    return 1;
  return collect_tier_key_active(stats->s_type, idx);
}

static void test_str_collect_key_list_uses_runtime_hook(void)
{
  setup_host_mem_type();
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  collect_set_key_active_hook(tier_key_active_hook, NULL);

  assert(str_collect_key_list("100 200 300 400", g_stats,
                              "mem_total", "mem_free", "active", "inactive",
                              NULL) == 4);
  assert(g_vals[0] == 100ULL);
  assert(g_vals[1] == 200ULL);
  assert(g_vals[2] == 300ULL);
  assert(g_vals[3] == 0ULL);

  collect_set_key_active_hook(NULL, NULL);
  schema_destroy(&g_type.st_schema);
}

static void test_tier_disabled_stores_all_keys(void)
{
  setup_host_mem_type();
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FAST_ONLY);

  stats_set(g_stats, "inactive", 77ULL);
  assert(g_vals[3] == 77ULL);

  collect_tier_set_enabled(1);
  schema_destroy(&g_type.st_schema);
}

int main(void)
{
  test_stats_set_skips_slow_on_fast_phase();
  test_stats_inc_skips_slow_on_fast_phase();
  test_str_collect_key_list_uses_runtime_hook();
  test_tier_disabled_stores_all_keys();
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);
  printf("test_stats_set_tier_gate passed\n");
  return 0;
}
