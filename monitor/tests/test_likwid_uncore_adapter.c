#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "cpuid.h"
#include "cpu_counter_metrics_likwid_begin.h"
#include "likwid_result_convert.h"
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
  (void)type;
  if (dev != NULL)
    snprintf(g_last_dev, sizeof(g_last_dev), "%s", dev);
  return &g_stats;
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static void emit_and_check(likwid_uncore_profile_t profile, const char *counter_name,
                           const char *expect_dev, const char *expect_key, unsigned long long val)
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

  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_SKX, "MBOX2C0", "mbox2", "dram_cas_reads", 100ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_SPR, "HBM3C1", "hbm3", "hbm_cas_writes", 200ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_ICX, "MDEV1C0", "mdev1", "dram_cas_reads", 300ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_ICX, "MBOX1C0", "mdev1", "dram_cas_reads", 301ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_IMC_ICX, "MBOX3C1", "mdev3", "dram_cas_writes", 302ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C0", "cbox4", "llc_lookup_data_read_local",
                 400ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C1", "cbox4", "sf_evictions_mes", 500ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C2:STATE=0x1F", "cbox4", "llc_lookup_write",
                 501ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SKX, "CBOX4C3", "cbox4", "bypass_cha_imc_all", 502ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_SPR, "CBOX1C2", "cbox1", "bypass_cha_imc_all", 503ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_CHA_GNR, "CBOX0C0", "cbox0", "llc_lookup_data_read_local",
                 504ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_DF_ROME, "DFC0", "df", "dram_chan0_bytes", 600ULL);
  emit_and_check(LIKWID_UNCORE_PROFILE_DF_TURIN, "UMC2C0", "df", "dram_chan2_bytes", 700ULL);

  test_stats_stub_unbind();
}

static void test_likwid_result_to_ull(void)
{
  unsigned long long out = 42;

  assert(likwid_result_to_ull(123.7, LIKWID_RESULT_U48_MAX, &out) == 0);
  assert(out == 123ULL);

  out = 99;
  assert(likwid_result_to_ull(0.0, LIKWID_RESULT_U48_MAX, &out) == 0);
  assert(out == 0ULL);

  out = 1;
  assert(likwid_result_to_ull(NAN, LIKWID_RESULT_U48_MAX, &out) < 0);
  assert(out == 1); /* unchanged on failure */

  assert(likwid_result_to_ull(-1.0, LIKWID_RESULT_U48_MAX, &out) < 0);
  assert(likwid_result_to_ull(INFINITY, LIKWID_RESULT_U48_MAX, &out) < 0);
  assert(likwid_result_to_ull(-INFINITY, LIKWID_RESULT_U48_MAX, &out) < 0);

  /* Above W=48 rejected (would become 2^63-style poison if cast blindly from NaN). */
  assert(likwid_result_to_ull((double)LIKWID_RESULT_U48_MAX + 1.0, LIKWID_RESULT_U48_MAX, &out) <
         0);
  assert(likwid_result_to_ull((double)LIKWID_RESULT_U48_MAX, LIKWID_RESULT_U48_MAX, &out) == 0);
  assert(out == LIKWID_RESULT_U48_MAX);

  /* PMC path: no upper bound. */
  assert(likwid_result_to_ull((double)LIKWID_RESULT_U48_MAX + 100.0, ~(unsigned long long)0,
                              &out) == 0);
  assert(out == LIKWID_RESULT_U48_MAX + 100ULL);

  assert(likwid_result_to_ull(1.0, LIKWID_RESULT_U48_MAX, NULL) < 0);

  /* Classic NaN→ull poison bit pattern must never be produced by the helper. */
  assert(likwid_result_to_ull(NAN, ~(unsigned long long)0, &out) < 0);
}

int main(void)
{
  test_emit_imc_and_cha_counters();
  test_likwid_result_to_ull();
  printf("test_likwid_uncore_adapter passed\n");
  return 0;
}
