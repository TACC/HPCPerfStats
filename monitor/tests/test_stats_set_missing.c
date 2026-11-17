/* stats_set_missing / stats_set_missing_all / stats_type_mark_not_updated: state byte contract. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
static struct stats_type g_type;

/* host_mem: inactive (idx 3) and dirty (idx 4) are slow-tier keys. */
static struct stats *setup_type(int tier_enabled)
{
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "host_mem");
  g_type.st_schema_def = "mem_total mem_free active inactive dirty";
  assert(stats_type_init(&g_type) == 0);
  collect_tier_set_enabled(tier_enabled);
  collect_tier_apply_to_type(&g_type);
  collect_tier_set_phase(COLLECT_FULL);
  return get_current_stats(&g_type, "-");
}

static void teardown_type(void)
{
  stats_type_destroy(&g_type);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);
}

static void test_new_device_starts_not_updated(void)
{
  struct stats *s = setup_type(0);

  assert(s != NULL);
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++) {
    assert(s->s_val_state[k] == STATS_SENTINEL_NOT_UPDATED);
    assert(s->s_val[k] == 0ULL);
  }
  teardown_type();
}

static void test_set_missing_keeps_value_and_set_clears(void)
{
  struct stats *s = setup_type(0);

  stats_set(s, "mem_free", 42ULL);
  assert(s->s_val_state[1] == STATS_SENTINEL_OK);

  stats_set_missing(s, "mem_free", STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[1] == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val[1] == 42ULL);

  stats_set(s, "mem_free", 43ULL);
  assert(s->s_val_state[1] == STATS_SENTINEL_OK);
  assert(s->s_val[1] == 43ULL);

  stats_set_missing(s, "active", STATS_SENTINEL_NOT_PRESENT);
  stats_inc(s, "active", 5ULL);
  assert(s->s_val_state[2] == STATS_SENTINEL_OK);
  assert(s->s_val[2] == 5ULL);
  teardown_type();
}

static void test_set_missing_ignores_unknown_and_null(void)
{
  struct stats *s = setup_type(0);

  stats_set_missing(s, "no_such_key", STATS_SENTINEL_READ_FAILED);
  stats_set_missing(s, NULL, STATS_SENTINEL_READ_FAILED);
  stats_set_missing(NULL, "mem_free", STATS_SENTINEL_READ_FAILED);
  stats_set_missing_all(NULL, STATS_SENTINEL_READ_FAILED);
  stats_type_mark_not_updated(NULL);
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++)
    assert(s->s_val_state[k] == STATS_SENTINEL_NOT_UPDATED);
  teardown_type();
}

static void test_tier_gate_on_fast_phase(void)
{
  struct stats *s = setup_type(1);

  stats_set(s, "inactive", 7ULL);
  collect_tier_set_phase(COLLECT_FAST_ONLY);

  stats_set_missing(s, "inactive", STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[3] == STATS_SENTINEL_OK);

  stats_set_missing_all(s, STATS_SENTINEL_BACKEND_ERROR);
  assert(s->s_val_state[0] == STATS_SENTINEL_BACKEND_ERROR);
  assert(s->s_val_state[2] == STATS_SENTINEL_BACKEND_ERROR);
  assert(s->s_val_state[3] == STATS_SENTINEL_OK);
  assert(s->s_val_state[4] == STATS_SENTINEL_NOT_UPDATED);

  collect_tier_set_phase(COLLECT_FULL);
  stats_set_missing_all(s, STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[3] == STATS_SENTINEL_OK);
  assert(s->s_val_state[4] == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[0] == STATS_SENTINEL_BACKEND_ERROR);
  assert(s->s_val[3] == 7ULL);
  teardown_type();
}

/* A whole-source failure marks only keys no other source set this cycle. */
static void test_set_missing_all_keeps_values_and_codes(void)
{
  struct stats *s = setup_type(0);

  stats_set(s, "mem_total", 1ULL);
  stats_set_missing(s, "mem_free", STATS_SENTINEL_NOT_PRESENT);
  stats_set_missing_all(s, STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[0] == STATS_SENTINEL_OK);
  assert(s->s_val_state[1] == STATS_SENTINEL_NOT_PRESENT);
  assert(s->s_val_state[2] == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val_state[4] == STATS_SENTINEL_READ_FAILED);

  stats_set(s, "active", 3ULL);
  assert(s->s_val_state[2] == STATS_SENTINEL_OK);
  teardown_type();
}

static void test_cycle_reset_marks_active_keys_only(void)
{
  struct stats *s = setup_type(1);
  struct stats *s2 = get_current_stats(&g_type, "other");

  assert(s2 != NULL);
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++) {
    stats_set(s, g_type.st_schema.sc_ent[k]->se_key, 100ULL + k);
    stats_set(s2, g_type.st_schema.sc_ent[k]->se_key, 200ULL + k);
  }

  collect_tier_set_phase(COLLECT_FAST_ONLY);
  stats_type_mark_not_updated(&g_type);
  assert(s->s_val_state[0] == STATS_SENTINEL_NOT_UPDATED);
  assert(s2->s_val_state[1] == STATS_SENTINEL_NOT_UPDATED);
  assert(s->s_val_state[3] == STATS_SENTINEL_OK);
  assert(s2->s_val_state[4] == STATS_SENTINEL_OK);

  collect_tier_set_phase(COLLECT_FULL);
  stats_type_mark_not_updated(&g_type);
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++) {
    assert(s->s_val_state[k] == STATS_SENTINEL_NOT_UPDATED);
    assert(s2->s_val_state[k] == STATS_SENTINEL_NOT_UPDATED);
    assert(s->s_val[k] == 100ULL + k);
  }

  /* stats_inc accumulators continue from s_val across the reset. */
  stats_inc(s, "mem_total", 1ULL);
  assert(s->s_val[0] == 101ULL);
  assert(s->s_val_state[0] == STATS_SENTINEL_OK);
  teardown_type();
}

static void test_type_set_missing_all_marks_every_row(void)
{
  struct stats *s = setup_type(0);
  struct stats *s2 = get_current_stats(&g_type, "row2");

  stats_set(s, "mem_total", 1ULL);
  stats_type_set_missing_all(&g_type, STATS_SENTINEL_BACKOFF);
  stats_type_set_missing_all(NULL, STATS_SENTINEL_BACKOFF);
  assert(s->s_val_state[0] == STATS_SENTINEL_OK);
  assert(s->s_val_state[1] == STATS_SENTINEL_BACKOFF);
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++)
    assert(s2->s_val_state[k] == STATS_SENTINEL_BACKOFF);
  teardown_type();
}

int main(void)
{
  test_new_device_starts_not_updated();
  test_set_missing_keeps_value_and_set_clears();
  test_set_missing_ignores_unknown_and_null();
  test_tier_gate_on_fast_phase();
  test_set_missing_all_keeps_values_and_codes();
  test_cycle_reset_marks_active_keys_only();
  test_type_set_missing_all_marks_every_row();
  printf("test_stats_set_missing passed\n");
  return 0;
}
