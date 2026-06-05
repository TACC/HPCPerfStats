#ifndef TEST_STATS_STUB_H_
#define TEST_STATS_STUB_H_

#include <stddef.h>

#include "stats.h"

#define TEST_STATS_STUB_MAX 64

struct test_stats_stub {
  char key[TEST_STATS_STUB_MAX][64];
  unsigned long long val[TEST_STATS_STUB_MAX];
  int n;
};

void test_stats_stub_reset(struct test_stats_stub *stub);
void test_stats_set_stub(struct stats *stats, const char *key, unsigned long long val);
int test_stats_stub_find(const struct test_stats_stub *stub, const char *key,
                         unsigned long long *out);

void test_stats_stub_bind(struct test_stats_stub *stub);
void test_stats_stub_unbind(void);

#endif
