/*
 * stats_buffer_collect payload assembly (STATS_BUFFER_TEST_SEND_HOOK; no live broker).
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpuid.h"
#include "collect_tier.h"
#include "stats_buffer.h"
#include "test_stats_buffer_collect_stubs.h"

char jobid[80] = "job42";
double send_freq = 1.0;
int nr_cpus = 1;
int n_pmcs = 0;
processor_t processor = (processor_t)0;

int stats_buffer_test_send_hook(struct stats_buffer *sf)
{
  (void)sf;
  return 0;
}

static void test_collect_fast_phase_sparse_rows(void)
{
  struct stats_buffer_collect_fixture fx;
  struct stats_buffer sf;
  const unsigned long long vals[2] = {11, 22};

  assert(stats_buffer_collect_fixture_init(&fx, "a,E b,E,R=S", vals, 2) == 0);
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  memset(&sf, 0, sizeof(sf));
  assert(stats_buffer_open(&sf, "127.0.0.1", "5672", "q", "u", "p") == 0);
  free(sf.sf_data);
  sf.sf_data = strdup("");
  assert(sf.sf_data != NULL);
  sf.sf_data_cap = 1;
  sf.sf_data_len = 0;

  assert(stats_buffer_collect(&sf) == 0);
  assert(strstr(sf.sf_data, "job42") != NULL);
  assert(strstr(sf.sf_data, "host_tt dev0 @fast 11") != NULL);
  assert(strstr(sf.sf_data, " 22") == NULL);

  stats_buffer_close(&sf);
  stats_buffer_collect_fixture_teardown(&fx);
}

static void test_collect_full_phase_emits_slow_keys(void)
{
  struct stats_buffer_collect_fixture fx;
  struct stats_buffer sf;
  const unsigned long long vals[2] = {11, 22};

  assert(stats_buffer_collect_fixture_init(&fx, "a,E b,E,R=S", vals, 2) == 0);
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FULL);
  memset(&sf, 0, sizeof(sf));
  assert(stats_buffer_open(&sf, "127.0.0.1", "5672", "q", "u", "p") == 0);
  free(sf.sf_data);
  sf.sf_data = strdup("");
  assert(sf.sf_data != NULL);
  sf.sf_data_cap = 1;
  sf.sf_data_len = 0;

  assert(stats_buffer_collect(&sf) == 0);
  assert(strstr(sf.sf_data, "host_tt dev0 @full 11 22") != NULL);

  stats_buffer_close(&sf);
  stats_buffer_collect_fixture_teardown(&fx);
}

int main(void)
{
  test_collect_fast_phase_sparse_rows();
  test_collect_full_phase_emits_slow_keys();
  printf("test_stats_buffer_collect passed\n");
  return 0;
}
