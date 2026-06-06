#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpuid.h"
#include "cpu_counter_metrics_likwid_begin.h"
#include "likwid_uncore_adapter.h"
#include "test_stats_stub.h"

processor_t processor = CASCADE_LAKE;

int cpu_counter_metrics_likwid_ready(void)
{
  return 0;
}

static struct stats g_stats;
static struct test_stats_stub g_stub;
static struct stats_type g_type;
static char g_last_dev[32];

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void) type;
  if (dev != NULL)
    snprintf(g_last_dev, sizeof(g_last_dev), "%s", dev);
  return &g_stats;
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static void emit_and_check(likwid_uncore_profile_t profile,
                           const char *counter_name,
                           const char *expect_dev, const char *expect_key,
                           unsigned long long val)
{
  unsigned long long out = 0;

  g_last_dev[0] = '\0';
  test_stats_stub_reset(&g_stub);
  likwid_uncore_adapter_emit_counter(&g_type, profile, counter_name, val);
  assert(strcmp(g_last_dev, expect_dev) == 0);
  assert(test_stats_stub_find(&g_stub, expect_key, &out));
  assert(out == val);
}

static void test_emit_imc_and_cha_counters(void)
{
  test_stats_stub_bind(&g_stub);

  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_SKX, "MBOX2C0", "mbox2",
                 "dram_cas_reads", 100ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_SPR, "HBM3C1", "hbm3",
                 "hbm_cas_writes", 200ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_ICX, "MDEV1C0", "mdev1",
                 "dram_cas_reads", 300ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C0", "cbox4",
                 "llc_lookup_data_read_local", 400ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C1", "cbox4",
                 "sf_evictions_mes", 500ULL);

  test_stats_stub_unbind();
}

int main(void)
{
  test_emit_imc_and_cha_counters();
  printf("test_likwid_uncore_adapter passed\n");
  return 0;
}
