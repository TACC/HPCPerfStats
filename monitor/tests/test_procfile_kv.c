#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "procfile_kv.h"
#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy_stats;

static void test_proc_kv_success(void)
{
  struct test_stats_stub stub;
  char line[] = "rx_bytes 100";
  unsigned long long val;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  assert(proc_kv_into_stats(&g_dummy_stats, line) == 0);
  assert(test_stats_stub_find(&stub, "rx_bytes", &val));
  assert(val == 100ULL);

  test_stats_stub_unbind();
}

static void test_proc_kv_parse_failure(void)
{
  struct test_stats_stub stub;
  char no_value[] = "key_only";

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  assert(proc_kv_into_stats(&g_dummy_stats, no_value) == -1);
  assert(stub.n == 0);

  test_stats_stub_unbind();
}

static void test_proc_kv_null_args(void)
{
  struct test_stats_stub stub;
  char whitespace[] = "   ";

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  assert(proc_kv_into_stats(&g_dummy_stats, whitespace) == -1);
  assert(stub.n == 0);

  test_stats_stub_unbind();
}

int main(void)
{
  struct test_stats_stub stub;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  test_proc_kv_success();
  test_proc_kv_parse_failure();
  test_proc_kv_null_args();

  test_stats_stub_unbind();
  printf("test_procfile_kv passed\n");
  return 0;
}
