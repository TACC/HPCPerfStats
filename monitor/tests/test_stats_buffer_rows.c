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
  s->s_val_present = malloc(n);
  assert(s->s_val != NULL && s->s_val_present != NULL);
  for (k = 0; k < n; k++) {
    s->s_val[k] = (k < nvals) ? vals[k] : 0ULL;
    s->s_val_present[k] = 1;
  }
  strcpy(s->s_dev, dev);
  return s;
}

static void free_test_stats(struct stats *s)
{
  if (s == NULL)
    return;
  free(s->s_val);
  free(s->s_val_present);
  free(s);
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
  const unsigned long long vals[4] = { 10, 20, 30, 40 };
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
  struct stats *s = key_to_stats((const char *) key);

  if (s == NULL)
    return;
  free(s->s_val);
  free(s->s_val_present);
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

int main(void)
{
  test_append_fast_tier_rows();
  test_append_full_tier_rows();
  test_skips_disabled_type();
  printf("test_stats_buffer_rows passed\n");
  return 0;
}
