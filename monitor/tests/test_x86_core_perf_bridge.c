/* x86_core_perf sentinel bridge: a key's lifetime bridges a READ_FAILED gap exactly (the next
 * good delta spans the gap), a re-seed or an unscheduled interval adds nothing, and the reader's
 * lifetime never drops or wraps (W=48 masking is the publisher's job). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "test_fake_perf.h"
#include "x86_core_perf.h"

#define CPU_TYPE 4
#define WRAP48 (1ULL << 48)

static char g_root[512];
static char g_dev[600];

/* AMD Genoa tree, one CPU; returns the fake call index of the cycles counter (raw 0x76). */
static int begin_genoa(void)
{
  struct x86_core_perf_cfg c;
  int cyc;

  fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_dev, sizeof(g_dev), "%s/devices", g_root);
  fake_sysfs_write(g_root, "devices/cpu/type", "4\n");
  fake_sysfs_write(g_root, "devices/cpu/format/event", "config:0-7,32-35\n");
  fake_sysfs_write(g_root, "devices/cpu/format/umask", "config:8-15\n");
  fake_sysfs_write(g_root, "devices/cpu/events/instructions", "event=0xc0\n");
  fake_sysfs_write(g_root, "devices/cpu/events/cpu-cycles", "event=0x76\n");
  memset(&c, 0, sizeof(c));
  c.devices_dir = g_dev;
  c.open_fn = fake_perf_open;
  fake_perf_reset();
  assert(x86_core_perf_begin(1, AMD_GENOA, &c) == 0);
  cyc = fake_perf_find(CPU_TYPE, 0x76, 0);
  assert(cyc >= 0);
  return cyc;
}

/* Pushes one cumulative sample for @p idx and collects CPU 0 into @p m. */
static void tick(int idx, uint64_t value, uint64_t enabled, uint64_t running,
                 struct x86_core_counts *m)
{
  fake_perf_push(idx, value, enabled, running);
  x86_core_perf_collect_cpu(0, m);
}

/* Three failed reads, then success: the delta after the gap carries the gap's activity. */
static void test_read_failure_gap_bridges(void)
{
  struct x86_core_counts m;
  unsigned long long before;
  int cyc = begin_genoa(), i;

  tick(cyc, 0, 0, 0, &m); /* seed */
  tick(cyc, 1000, 1000, 1000, &m);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_OK && m.life[X86_CORE_CYCLES] == 1000ULL);
  before = m.life[X86_CORE_CYCLES];
  for (i = 0; i < 3; i++) { /* nothing pushed: the read fails */
    x86_core_perf_collect_cpu(0, &m);
    assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_READ_FAILED);
    assert(m.have[X86_CORE_CYCLES] && m.life[X86_CORE_CYCLES] == before);
  }
  /* 4 intervals of 1000 cycles each (3 in the gap), half multiplexed: 2000 x 4000 / 2000. */
  tick(cyc, 3000, 5000, 3000, &m);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_OK);
  assert(m.life[X86_CORE_CYCLES] == before + 4000ULL);
  assert(m.pmu_enabled_ns >= 5000ULL); /* the gap's enabled time is not lost either */
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* A counter going backwards re-seeds: COUNTER_RESET adds 0, life never drops, and the next
 * delta counts from the new base. */
static void test_reseed_adds_zero(void)
{
  struct x86_core_counts m;
  unsigned long long before;
  int cyc = begin_genoa();

  tick(cyc, 0, 0, 0, &m);
  tick(cyc, 5000, 1000, 1000, &m);
  before = m.life[X86_CORE_CYCLES];
  assert(before == 5000ULL);
  tick(cyc, 10, 2000, 2000, &m);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_COUNTER_RESET);
  assert(m.life[X86_CORE_CYCLES] == before);
  tick(cyc, 60, 3000, 3000, &m);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_OK && m.life[X86_CORE_CYCLES] == before + 50);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* Enabled but never on a counter: NOT_SCHEDULED, nothing added (activity unmeasured). */
static void test_not_scheduled_adds_zero(void)
{
  struct x86_core_counts m;
  int cyc = begin_genoa();

  tick(cyc, 0, 0, 0, &m);
  tick(cyc, 700, 1000, 1000, &m);
  tick(cyc, 700, 2000, 1000, &m);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_NOT_SCHEDULED);
  assert(m.life[X86_CORE_CYCLES] == 700ULL);
  tick(cyc, 900, 3000, 2000, &m); /* running again: 200 x 1000 / 1000 */
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_OK && m.life[X86_CORE_CYCLES] == 900ULL);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* The reader's lifetime is 64-bit: it passes 2^48 without wrapping or masking. */
static void test_life_grows_past_2_pow_48(void)
{
  struct x86_core_counts m;
  int cyc = begin_genoa();

  tick(cyc, 0, 0, 0, &m);
  tick(cyc, WRAP48 - 10, 1000, 1000, &m);
  assert(m.life[X86_CORE_CYCLES] == WRAP48 - 10);
  tick(cyc, WRAP48 + 5, 2000, 2000, &m);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_OK);
  assert(m.life[X86_CORE_CYCLES] == WRAP48 + 5);
  tick(cyc, 2 * WRAP48 + 5, 3000, 3000, &m);
  assert(m.life[X86_CORE_CYCLES] == 2 * WRAP48 + 5);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

int main(void)
{
  fake_sysfs_init(g_root, sizeof(g_root));
  test_read_failure_gap_bridges();
  test_reseed_adds_zero();
  test_not_scheduled_adds_zero();
  test_life_grows_past_2_pow_48();
  fake_sysfs_rm(g_root);
  printf("test_x86_core_perf_bridge passed\n");
  return 0;
}
