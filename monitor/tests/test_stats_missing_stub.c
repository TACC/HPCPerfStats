#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "test_stats_missing_stub.h"

#define TEST_STATS_MISSING_MAX 128

static char g_keys[TEST_STATS_MISSING_MAX][64];
static unsigned int g_codes[TEST_STATS_MISSING_MAX];
static int g_n;

static void test_stats_missing_record(const char *key, enum stats_sentinel code)
{
  int i;

  for (i = 0; i < g_n; i++) {
    if (strcmp(g_keys[i], key) == 0) {
      g_codes[i] = (unsigned int)code;
      return;
    }
  }
  assert(g_n < TEST_STATS_MISSING_MAX);
  snprintf(g_keys[g_n], sizeof(g_keys[0]), "%s", key);
  g_codes[g_n++] = (unsigned int)code;
}

void(stats_set_missing)(struct stats *stats, const char *key, enum stats_sentinel code)
{
  (void)stats;
  if (key != NULL)
    test_stats_missing_record(key, code);
}

void(stats_set_missing_all)(struct stats *stats, enum stats_sentinel code)
{
  (void)stats;
  test_stats_missing_record(TEST_STATS_MISSING_ALL_KEY, code);
}

void test_stats_missing_reset(void)
{
  g_n = 0;
}

unsigned int test_stats_missing_code(const char *key)
{
  int i;

  for (i = 0; i < g_n; i++)
    if (strcmp(g_keys[i], key) == 0)
      return g_codes[i];
  return 0;
}

int test_stats_missing_count(void)
{
  return g_n;
}
