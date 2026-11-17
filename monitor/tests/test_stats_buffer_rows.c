/* stats_buffer_append_enabled_type_rows: batched tier row assembly into sf_data. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collect_tier.h"
#include "dict.h"
#include "schema.h"
#include "stats.h"
#include "stats_buffer.h"
#include "stats_buffer_rows.h"
#include "monitor_release_log.h"
#include "stats_sentinel.h"
#include "stats_text_format.h"
#include "test_stats_stub.h"

char jobid[80] = "-";

static struct stats_type g_type;
static size_t g_type_iter;

static struct stats *make_test_stats(struct stats_type *type, const char *dev,
                                     const unsigned long long *vals, size_t nvals)
{
  size_t n = type->st_schema.sc_len;
  struct stats *s = malloc(sizeof(*s) + strlen(dev) + 1);
  size_t k;

  assert(s != NULL);
  s->s_type = type;
  s->s_val = malloc(n * sizeof(*s->s_val));
  s->s_val_state = malloc(n);
  assert(s->s_val != NULL && s->s_val_state != NULL);
  for (k = 0; k < n; k++) {
    s->s_val[k] = (k < nvals) ? vals[k] : 0ULL;
    s->s_val_state[k] = STATS_SENTINEL_OK;
  }
  strcpy(s->s_dev, dev);
  return s;
}

struct stats_type *stats_type_for_each(size_t *i)
{
  if (*i != 0)
    return NULL;
  (*i)++;
  return &g_type;
}

static void setup_type(void)
{
  const unsigned long long vals[4] = {10, 20, 30, 40};
  struct stats *s;

  memset(&g_type, 0, sizeof(g_type));
  g_type_iter = 0;
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "host_tt");
  assert(schema_init(&g_type.st_schema, "a,E b,E,R=S c,E d,E,R=S") == 0);
  g_type.st_enabled = 1;
  assert(dict_init(&g_type.st_current_dict, 4) == 0);
  s = make_test_stats(&g_type, "dev0", vals, 4);
  assert(dict_set(&g_type.st_current_dict, s->s_dev) == 0);
}

static void free_stats_dict_key(void *key)
{
  struct stats *s = key_to_stats((const char *)key);

  if (s == NULL)
    return;
  free(s->s_val);
  free(s->s_val_state);
  free(s);
}

static void teardown_type(void)
{
  schema_destroy(&g_type.st_schema);
  dict_destroy(&g_type.st_current_dict, free_stats_dict_key);
}

static void test_append_fast_tier_rows(void)
{
  struct stats_buffer sf;
  char *nl;

  setup_type();
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("");
  assert(sf.sf_data != NULL);
  sf.sf_data_cap = 1;

  stats_buffer_append_enabled_type_rows(&sf, STATS_ROW_FAST);
  assert(sf.sf_data_len > 0);
  assert(strstr(sf.sf_data, "host_tt dev0 @fast 10 30") != NULL);
  nl = strchr(sf.sf_data, '\n');
  assert(nl != NULL);

  free(sf.sf_data);
  teardown_type();
}

static void test_append_full_tier_rows(void)
{
  struct stats_buffer sf;

  setup_type();
  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("");
  assert(sf.sf_data != NULL);
  sf.sf_data_cap = 1;

  stats_buffer_append_enabled_type_rows(&sf, STATS_ROW_FULL);
  assert(strstr(sf.sf_data, "host_tt dev0 @full 10 20 30 40") != NULL);

  free(sf.sf_data);
  teardown_type();
}

static void test_skips_disabled_type(void)
{
  struct stats_buffer sf;

  setup_type();
  g_type.st_enabled = 0;
  memset(&sf, 0, sizeof(sf));
  sf.sf_data = strdup("");
  assert(sf.sf_data != NULL);
  sf.sf_data_cap = 1;

  stats_buffer_append_enabled_type_rows(&sf, STATS_ROW_FAST);
  assert(sf.sf_data_len == 0);

  free(sf.sf_data);
  teardown_type();
}

static struct stats *first_stats(void)
{
  size_t j = 0;

  return key_to_stats(dict_for_each(&g_type.st_current_dict, &j));
}

static void append_rows(struct stats_buffer *sf, enum stats_row_tier tier)
{
  memset(sf, 0, sizeof(*sf));
  sf->sf_data = strdup("");
  assert(sf->sf_data != NULL);
  sf->sf_data_cap = 1;
  stats_buffer_append_enabled_type_rows(sf, tier);
}

/* A non-OK state prints "-<code>" and never the stale s_val behind it. */
static void test_sentinel_state_prints_code_not_stale_value(void)
{
  struct stats_buffer sf;
  struct stats *s;

  setup_type();
  s = first_stats();
  s->s_val_state[1] = STATS_SENTINEL_READ_FAILED;
  s->s_val_state[3] = STATS_SENTINEL_NOT_BUILT;

  monitor_release_fail_reset_for_test();
  append_rows(&sf, STATS_ROW_FULL);
  assert(strstr(sf.sf_data, "host_tt dev0 @full 10 -3 30 -10\n") != NULL);
  assert(strstr(sf.sf_data, " 20") == NULL && strstr(sf.sf_data, " 40") == NULL);
  free(sf.sf_data);

  s->s_val_state[0] = STATS_SENTINEL_NOT_UPDATED;
  append_rows(&sf, STATS_ROW_FAST);
  assert(strstr(sf.sf_data, "host_tt dev0 @fast -1 30\n") != NULL);
  free(sf.sf_data);

  /* The release hourly log counts each emitted token once; slow keys on a fast row are not
   * emitted, so they are not counted. */
  {
    char line[256];

    assert(monitor_release_sentinel_summary(line, sizeof(line)) > 0);
    assert(strcmp(line, "sentinels: NOT_UPDATED=1 READ_FAILED=1 NOT_BUILT=1") == 0);
  }
  teardown_type();
}

/* emit_value_sentinels 0: every key prints s_val, byte-identical to the pre-sentinel row. */
static void test_knob_off_prints_legacy_values(void)
{
  struct stats_buffer sf;
  struct stats *s;

  setup_type();
  s = first_stats();
  for (size_t k = 0; k < g_type.st_schema.sc_len; k++)
    s->s_val_state[k] = STATS_SENTINEL_NOT_UPDATED;
  stats_format_set_value_sentinels(0);

  append_rows(&sf, STATS_ROW_FULL);
  assert(strcmp(sf.sf_data, "host_tt dev0 @full 10 20 30 40\n") == 0);
  free(sf.sf_data);
  append_rows(&sf, STATS_ROW_LEGACY);
  assert(strcmp(sf.sf_data, "host_tt dev0 10 20 30 40\n") == 0);
  free(sf.sf_data);

  stats_format_set_value_sentinels(1);
  teardown_type();
}

/* key_to_stats maps the dict key (s_dev) back to its stats (offsetof, not NULL-pointer
 * subtraction; clang -Wnull-pointer-subtraction). */
static void test_key_to_stats_round_trip(void)
{
  _Alignas(struct stats) char buf[sizeof(struct stats) + 16];
  struct stats *s = (struct stats *)buf;

  snprintf(s->s_dev, 16, "%s", "dev7");
  assert(key_to_stats(s->s_dev) == s);
}

int main(void)
{
  test_key_to_stats_round_trip();
  test_append_fast_tier_rows();
  test_append_full_tier_rows();
  test_skips_disabled_type();
  test_sentinel_state_prints_code_not_stale_value();
  test_knob_off_prints_legacy_values();
  printf("test_stats_buffer_rows passed\n");
  return 0;
}
