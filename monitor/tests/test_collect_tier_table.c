/* Static slow-key table + *_error(s) auto-rule + enable flag (collect_tier.c). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "collect_tier.h"
#include "schema.h"
#include "stats.h"

static void make_type(struct stats_type *type, const char *name, const char *def)
{
  memset(type, 0, sizeof(*type));
  snprintf(type->st_name, sizeof(type->st_name), "%s", name);
  assert(schema_init(&type->st_schema, def) == 0);
}

static int tier_of(struct stats_type *type, const char *key)
{
  int idx = schema_ref(&type->st_schema, key);
  assert(idx >= 0);
  return (int)type->st_schema.sc_ent[idx]->se_collect_tier;
}

static void test_disabled_keeps_all_fast(void)
{
  struct stats_type type;

  collect_tier_set_enabled(0);
  make_type(&type, "host_numa",
            "numa_hit,E numa_miss,E interleave_hit,E local_node,E other_node,E");
  collect_tier_apply_to_type(&type);
  assert(tier_of(&type, "numa_hit") == COLLECT_TIER_FAST);
  assert(tier_of(&type, "interleave_hit") == COLLECT_TIER_FAST);
  assert(tier_of(&type, "local_node") == COLLECT_TIER_FAST);
  schema_destroy(&type.st_schema);
}

static void test_enabled_applies_numa_table(void)
{
  struct stats_type type;

  collect_tier_set_enabled(1);
  make_type(&type, "host_numa",
            "numa_hit,E numa_miss,E numa_foreign,E interleave_hit,E local_node,E other_node,E");
  collect_tier_apply_to_type(&type);
  assert(tier_of(&type, "interleave_hit") == COLLECT_TIER_SLOW);
  assert(tier_of(&type, "local_node") == COLLECT_TIER_SLOW);
  assert(tier_of(&type, "numa_hit") == COLLECT_TIER_FAST);
  assert(tier_of(&type, "numa_miss") == COLLECT_TIER_FAST);
  assert(tier_of(&type, "numa_foreign") == COLLECT_TIER_FAST);
  assert(tier_of(&type, "other_node") == COLLECT_TIER_FAST);

  /* Idempotent: a second apply must not flip anything. */
  collect_tier_apply_to_type(&type);
  assert(tier_of(&type, "numa_hit") == COLLECT_TIER_FAST);
  assert(tier_of(&type, "local_node") == COLLECT_TIER_SLOW);
  schema_destroy(&type.st_schema);
}

static void test_error_suffix_rule(void)
{
  struct stats_type type;

  collect_tier_set_enabled(1);
  make_type(&type, "host_net", "rx_bytes,E rx_crc_errors,E tx_errors,E collisions,E rx_dropped,E");
  collect_tier_apply_to_type(&type);
  assert(tier_of(&type, "rx_crc_errors") == COLLECT_TIER_SLOW); /* *_errors rule */
  assert(tier_of(&type, "tx_errors") == COLLECT_TIER_SLOW);     /* *_errors rule */
  assert(tier_of(&type, "collisions") == COLLECT_TIER_SLOW);    /* static table */
  assert(tier_of(&type, "rx_dropped") == COLLECT_TIER_SLOW);    /* static table */
  assert(tier_of(&type, "rx_bytes") == COLLECT_TIER_FAST);      /* unknown -> fast */
  schema_destroy(&type.st_schema);
}

static void test_key_is_slow_helper(void)
{
  /* Independent of enable flag. */
  assert(collect_tier_key_is_slow("host_numa", "local_node") == 1);
  assert(collect_tier_key_is_slow("host_numa", "numa_hit") == 0);
  assert(collect_tier_key_is_slow("host_net", "tx_errors") == 1);
  assert(collect_tier_key_is_slow("host_net", "tx_error") == 1);
  assert(collect_tier_key_is_slow("host_net", "rx_packets") == 0);
  assert(collect_tier_key_is_slow("nonexistent_type", "whatever") == 0);
}

static void test_enabled_readback(void)
{
  collect_tier_set_enabled(1);
  assert(collect_tier_enabled() == 1);
  collect_tier_set_enabled(0);
  assert(collect_tier_enabled() == 0);
}

static void test_key_active_by_phase(void)
{
  struct stats_type type;
  int fast_idx, slow_idx;

  collect_tier_set_enabled(1);
  make_type(&type, "host_numa", "numa_hit,E interleave_hit,E");
  collect_tier_apply_to_type(&type);
  fast_idx = schema_ref(&type.st_schema, "numa_hit");
  slow_idx = schema_ref(&type.st_schema, "interleave_hit");

  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(collect_tier_key_active(&type, fast_idx) == 1);
  assert(collect_tier_key_active(&type, slow_idx) == 0);

  collect_tier_set_phase(COLLECT_FULL);
  assert(collect_tier_key_active(&type, fast_idx) == 1);
  assert(collect_tier_key_active(&type, slow_idx) == 1);

  /* Disabled: everything active regardless of phase. */
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(collect_tier_key_active(&type, slow_idx) == 1);

  schema_destroy(&type.st_schema);
}

int main(void)
{
  test_disabled_keeps_all_fast();
  test_enabled_applies_numa_table();
  test_error_suffix_rule();
  test_key_is_slow_helper();
  test_enabled_readback();
  test_key_active_by_phase();
  collect_tier_set_enabled(0);
  printf("test_collect_tier_table passed\n");
  return 0;
}
