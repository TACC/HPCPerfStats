#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "test_stats_stub.h"

static struct test_stats_stub *g_test_stats_stub;

void test_stats_stub_reset(struct test_stats_stub *stub)
{
  if (stub != NULL)
    stub->n = 0;
}

void test_stats_set_stub(struct stats *stats, const char *key, unsigned long long val)
{
  struct test_stats_stub *stub = g_test_stats_stub;
  int i;

  (void)stats;
  if (stub == NULL || key == NULL)
    return;
  for (i = 0; i < stub->n; i++) {
    if (strcmp(stub->key[i], key) == 0) {
      stub->val[i] = val;
      return;
    }
  }
  assert(stub->n < TEST_STATS_STUB_MAX);
  snprintf(stub->key[stub->n], sizeof(stub->key[0]), "%s", key);
  stub->val[stub->n] = val;
  stub->n++;
}

int test_stats_stub_find(const struct test_stats_stub *stub, const char *key,
                         unsigned long long *out)
{
  int i;

  if (stub == NULL || key == NULL)
    return 0;
  for (i = 0; i < stub->n; i++) {
    if (strcmp(stub->key[i], key) == 0) {
      if (out != NULL)
        *out = stub->val[i];
      return 1;
    }
  }
  return 0;
}

void test_stats_stub_bind(struct test_stats_stub *stub)
{
  g_test_stats_stub = stub;
}

void test_stats_stub_unbind(void)
{
  g_test_stats_stub = NULL;
}
