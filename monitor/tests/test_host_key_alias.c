#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "host_key_alias.h"
#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy_stats;

static void test_lookup(void)
{
  assert(host_key_alias_lookup("MemTotal") != NULL);
  assert(strcmp(host_key_alias_lookup("MemTotal"), "mem_total") == 0);
  assert(host_key_alias_lookup("VmRSS") != NULL);
  assert(strcmp(host_key_alias_lookup("VmRSS"), "vm_rss") == 0);
  assert(host_key_alias_lookup("UnknownField") == NULL);
  assert(host_key_alias_lookup(NULL) == NULL);
  assert(host_key_alias_lookup("") == NULL);
}

static void test_emit(void)
{
  struct test_stats_stub stub;
  unsigned long long val;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  host_key_alias_emit(&g_dummy_stats, "MemFree", 4096ULL);
  assert(test_stats_stub_find(&stub, "mem_free", &val));
  assert(val == 4096ULL);

  host_key_alias_emit(&g_dummy_stats, "not_mapped", 99ULL);
  assert(stub.n == 1);

  host_key_alias_emit(NULL, "MemTotal", 1ULL);
  assert(stub.n == 1);

  test_stats_stub_unbind();
}

int main(void)
{
  struct test_stats_stub stub;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  test_lookup();
  test_emit();

  test_stats_stub_unbind();
  printf("test_host_key_alias passed\n");
  return 0;
}
