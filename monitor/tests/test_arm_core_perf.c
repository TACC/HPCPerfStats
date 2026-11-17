/* arm_core_perf: Grace core event resolution (O12) and scaled per-CPU lifetimes.
 *
 * Finding #8 (c122-018, Neoverse V2 MIDR 0x410fd4f0): the kernel names only cpu_cycles (0x11)
 * and inst_retired (0x08); the seven FP / SVE INT events come from the MIDR raw-code table.
 * Regression for the 2026-11-13 "always zero" finding: the Grace FP keys were never programmed. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "arm_core_perf.h"
#include "test_fake_perf.h"

#define CORE_TYPE 8

static char g_root[512];
static char g_dev[600];
static char g_midr[600];
static char g_cpuinfo[600];

static struct arm_core_perf_cfg cfg(void)
{
  struct arm_core_perf_cfg c;

  memset(&c, 0, sizeof(c));
  c.devices_dir = g_dev;
  c.midr_path = g_midr;
  c.cpuinfo_path = g_cpuinfo;
  c.open_fn = fake_perf_open;
  return c;
}

/* Grace-like sysfs: armv8_pmuv3_0 names only cpu_cycles / inst_retired. */
static void make_tree(const char *midr)
{
  fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_dev, sizeof(g_dev), "%s/devices", g_root);
  snprintf(g_midr, sizeof(g_midr), "%s/midr_el1", g_root);
  snprintf(g_cpuinfo, sizeof(g_cpuinfo), "%s/cpuinfo", g_root);
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/type", "8\n");
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/format/event", "config:0-15\n");
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/events/cpu_cycles", "event=0x0011\n");
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/events/inst_retired", "event=0x0008\n");
  fake_sysfs_write(g_root, "devices/nvidia_scf_pmu_0/type", "25\n");
  if (midr != NULL)
    fake_sysfs_write(g_root, "midr_el1", midr);
}

static void test_read_midr_sysfs_then_cpuinfo(void)
{
  unsigned long long m = 0;

  make_tree("0x00000000410fd4f0\n");
  assert(arm_core_perf_read_midr(g_midr, g_cpuinfo, &m) == 0 && m == 0x410fd4f0ULL);
  /* midr_el1 absent: rebuilt from /proc/cpuinfo implementer / part. */
  make_tree(NULL);
  fake_sysfs_write(g_root, "cpuinfo",
                   "processor\t: 0\nBogoMIPS\t: 2000.00\nCPU implementer\t: 0x41\n"
                   "CPU architecture: 8\nCPU variant\t: 0x0\nCPU part\t: 0xd4f\n");
  assert(arm_core_perf_read_midr(g_midr, g_cpuinfo, &m) == 0);
  assert(m == ((0x41ULL << 24) | (0xd4fULL << 4)));
  /* Neither source: error. */
  make_tree(NULL);
  assert(arm_core_perf_read_midr(g_midr, g_cpuinfo, &m) == -1);
  assert(arm_core_perf_read_midr(g_midr, g_cpuinfo, NULL) == -1);
}

static void test_resolve_neoverse_v2_table(void)
{
  struct arm_core_perf_cfg c;
  struct perf_event_attr a[ARM_CORE_NEVENTS];
  int ok[ARM_CORE_NEVENTS];
  unsigned long long midr = 0;
  static const unsigned long long want[ARM_CORE_NEVENTS] = {0x11,   0x08,   0x801C, 0x8018, 0x8014,
                                                            0x80C1, 0x80C0, 0x80E3, 0x80E7};
  int e;

  make_tree("0x00000000410fd4f0\n");
  c = cfg();
  assert(arm_core_perf_resolve(&c, a, ok, &midr) == ARM_CORE_NEVENTS);
  assert(midr == 0x410fd4f0ULL);
  for (e = 0; e < ARM_CORE_NEVENTS; e++) {
    assert(ok[e]);
    assert(a[e].type == CORE_TYPE); /* armv8_pmuv3 type from sysfs, not PERF_TYPE_RAW */
    assert(a[e].config == want[e]);
    assert(a[e].read_format == (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING));
  }
  /* A sysfs name wins over the table when the kernel exposes it. */
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/events/fp_dp_spec", "event=0x4242\n");
  assert(arm_core_perf_resolve(&c, a, ok, NULL) == ARM_CORE_NEVENTS);
  assert(a[ARM_CORE_FP_DP].config == 0x4242);
}

static void test_resolve_unknown_midr_only_named_events(void)
{
  struct arm_core_perf_cfg c;
  struct perf_event_attr a[ARM_CORE_NEVENTS];
  int ok[ARM_CORE_NEVENTS];
  unsigned long long midr = 0;
  int e;

  make_tree("0x00000000410fd400\n"); /* part 0xd40: no table row */
  c = cfg();
  assert(arm_core_perf_resolve(&c, a, ok, &midr) == 2);
  assert(ok[ARM_CORE_CYC] && ok[ARM_CORE_INS]);
  for (e = ARM_CORE_FP_DP; e < ARM_CORE_NEVENTS; e++)
    assert(!ok[e]);
  /* No core PMU at all. */
  fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_dev, sizeof(g_dev), "%s/devices", g_root);
  fake_sysfs_mkdir(g_root, "devices/nvidia_scf_pmu_0");
  assert(arm_core_perf_resolve(&c, a, ok, &midr) == -1);
  assert(arm_core_perf_resolve(&c, NULL, ok, &midr) == -1);
}

/* Seeds every open counter, then pushes one multiplexed tick: value 100 x (e+1) at 25%. */
static void push_tick(void)
{
  int i;

  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 0, 0, 0);
  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 100ULL * (g_fake_perf_calls[i].config & 0xf) + 100ULL, 400, 100);
}

static void test_begin_collect_scaled_lifetimes(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;
  int cpu, e, i;

  make_tree("0x00000000410fd4f0\n");
  fake_perf_reset();
  c = cfg();
  assert(arm_core_perf_begin(2, &c) == 0);
  assert(arm_core_perf_ready());
  assert(g_fake_perf_n == 2 * ARM_CORE_NEVENTS);
  for (i = 0; i < g_fake_perf_n; i++) {
    assert(g_fake_perf_calls[i].pinned == 0 && g_fake_perf_calls[i].exclusive == 0);
    assert(g_fake_perf_calls[i].cpu == 0 || g_fake_perf_calls[i].cpu == 1);
  }
  push_tick();
  for (cpu = 0; cpu < 2; cpu++) {
    arm_core_perf_collect_cpu(cpu, &m); /* seed */
    arm_core_perf_collect_cpu(cpu, &m); /* scaled tick */
    for (e = 0; e < ARM_CORE_NEVENTS; e++)
      assert(m.have[e]);
  }
  /* CYC (config 0x11): raw 200 at 25% running -> 800; FP_DP (0x801C): 1300 -> 5200. */
  assert(m.life[ARM_CORE_CYC] == 800ULL);
  assert(m.life[ARM_CORE_FP_DP] == 5200ULL);
  /* Out-of-range CPU and NULL out are safe. */
  arm_core_perf_collect_cpu(5, &m);
  assert(m.have[ARM_CORE_CYC] == 0 && m.life[ARM_CORE_CYC] == 0);
  arm_core_perf_collect_cpu(0, NULL);
  arm_core_perf_cleanup();
  for (i = 0; i < g_fake_perf_n; i++)
    assert(fake_perf_closed(i));
  assert(!arm_core_perf_ready());
  fake_perf_reset();
}

static void test_one_event_failing_leaves_others(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  make_tree("0x00000000410fd4f0\n");
  fake_perf_reset();
  fake_perf_fail(0x8018, -1); /* FP_SP_SPEC fails on every CPU */
  c = cfg();
  assert(arm_core_perf_begin(2, &c) == 0);
  assert(g_fake_perf_n == 2 * (ARM_CORE_NEVENTS - 1));
  push_tick();
  arm_core_perf_collect_cpu(1, &m);
  arm_core_perf_collect_cpu(1, &m);
  assert(!m.have[ARM_CORE_FP_SP] && m.life[ARM_CORE_FP_SP] == 0);
  assert(m.have[ARM_CORE_CYC] && m.have[ARM_CORE_FP_DP] && m.have[ARM_CORE_SVE_INT16]);
  arm_core_perf_cleanup();
  fake_perf_reset();
  /* Partial: CYC fails only on CPU 1 (e.g. offline). */
  fake_perf_fail(0x11, 1);
  assert(arm_core_perf_begin(2, &c) == 0);
  arm_core_perf_collect_cpu(1, &m);
  assert(!m.have[ARM_CORE_CYC] && m.have[ARM_CORE_INS]);
  arm_core_perf_cleanup();
  fake_perf_reset();
}

/* An open errno maps to its sentinel code (EACCES PERMISSION_DENIED, EBUSY PMU_BUSY,
 * ENOENT/EINVAL NOT_PRESENT), an unnamed event is NOT_PRESENT, a failed read is READ_FAILED, and a
 * lifetime never decreases across a failed read, a re-seed or an unscheduled interval. */
static void test_event_sentinel_codes(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;
  unsigned long long life_before;
  int cyc, ins;

  make_tree("0x00000000410fd4f0\n");
  fake_perf_reset();
  fake_perf_fail_errno(0x8018, -1, EACCES); /* FP_SP_SPEC */
  c = cfg();
  assert(arm_core_perf_begin(1, &c) == 0);
  cyc = fake_perf_find(CORE_TYPE, 0x11, 0);
  ins = fake_perf_find(CORE_TYPE, 0x08, 0);
  assert(cyc >= 0 && ins >= 0);
  arm_core_perf_collect_cpu(0, &m); /* empty pipes: every open event READ_FAILED */
  assert(m.code[ARM_CORE_FP_SP] == STATS_SENTINEL_PERMISSION_DENIED && !m.have[ARM_CORE_FP_SP]);
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_READ_FAILED);
  fake_perf_push(cyc, 0, 0, 0);
  fake_perf_push(ins, 0, 0, 0);
  arm_core_perf_collect_cpu(0, &m); /* seed */
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_OK && m.code[ARM_CORE_INS] == STATS_SENTINEL_OK);
  fake_perf_push(cyc, 100, 1000, 500); /* partial multiplexing: scaled, no sentinel */
  fake_perf_push(ins, 100, 1000, 0);   /* never scheduled */
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_OK && m.life[ARM_CORE_CYC] == 200ULL);
  assert(m.code[ARM_CORE_INS] == STATS_SENTINEL_NOT_SCHEDULED && m.life[ARM_CORE_INS] == 0ULL);
  life_before = m.life[ARM_CORE_CYC];
  fake_perf_push(cyc, 10, 2000, 1000); /* counter went backwards: re-seed */
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_COUNTER_RESET);
  assert(m.life[ARM_CORE_CYC] == life_before);
  arm_core_perf_collect_cpu(0, &m); /* failed read keeps prev */
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_READ_FAILED && m.life[ARM_CORE_CYC] == life_before);
  fake_perf_push(cyc, 110, 4000, 2000); /* spans the failed tick: 100 x 2000 / 1000 */
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_OK && m.life[ARM_CORE_CYC] == life_before + 200ULL);
  arm_core_perf_cleanup();
  fake_perf_reset();

  fake_perf_fail_errno(0x8018, -1, EBUSY);
  assert(arm_core_perf_begin(1, &c) == 0);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_FP_SP] == STATS_SENTINEL_PMU_BUSY);
  arm_core_perf_cleanup();
  fake_perf_reset();

  fake_perf_fail_errno(0x8018, -1, EINVAL);
  assert(arm_core_perf_begin(1, &c) == 0);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_FP_SP] == STATS_SENTINEL_NOT_PRESENT);
  arm_core_perf_cleanup();
  fake_perf_reset();

  /* Unknown MIDR: the table events are unnamed, so NOT_PRESENT; out-of-range CPU likewise. */
  make_tree("0x00000000deadbeef\n");
  assert(arm_core_perf_begin(1, &c) == 0);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.code[ARM_CORE_FP_DP] == STATS_SENTINEL_NOT_PRESENT);
  arm_core_perf_collect_cpu(3, &m);
  assert(m.code[ARM_CORE_CYC] == STATS_SENTINEL_NOT_PRESENT);
  arm_core_perf_cleanup();
  fake_perf_reset();
}

/* Seeds every open counter, then pushes one tick of 1000 ns enabled: the cycle event (0x11, on
 * the dedicated cycle counter) runs @p cyc_run ns, every other event @p gen_run ns. */
static void push_sharing_tick(uint64_t cyc_run, uint64_t gen_run)
{
  int i;

  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 0, 0, 0);
  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 10, 1000, g_fake_perf_calls[i].config == 0x11 ? cyc_run : gen_run);
}

/* D6 (Phase 1b) design correction: the monitor alone oversubscribes Grace's 6 general counters
 * with 8 events (CYC sits on the cycle counter), so its events run ~75% with no other program.
 * That must read as "no outside sharing": running time equals the solo-expected time. */
static void test_sharing_own_oversubscription_only(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  make_tree("0x00000000410fd4f0\n"); /* Neoverse V2: 6 general counters */
  fake_perf_reset();
  c = cfg();
  assert(arm_core_perf_begin(1, &c) == 0);
  push_sharing_tick(1000, 750);
  arm_core_perf_collect_cpu(0, &m); /* seed */
  arm_core_perf_collect_cpu(0, &m);
  assert(m.have_times && m.have_solo);
  assert(m.pmu_enabled_ns == 9000ULL);           /* 9 events x 1000 ns */
  assert(m.pmu_running_ns == 1000ULL + 8 * 750); /* 7000 */
  assert(m.pmu_solo_ns == 1000ULL + 8 * 750);    /* CYC 1000 + 8 x 1000 x 6/8 */
  arm_core_perf_cleanup();
  fake_perf_reset();
  /* Seven general events open (FP_SP failed): solo share 6/7, so 857 of every 1000 ns. */
  fake_perf_fail(0x8018, -1);
  assert(arm_core_perf_begin(1, &c) == 0);
  push_sharing_tick(1000, 857);
  arm_core_perf_collect_cpu(0, &m);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.pmu_enabled_ns == 8000ULL && m.pmu_running_ns == 1000ULL + 7 * 857);
  assert(m.pmu_solo_ns == 1000ULL + 6000ULL); /* 7 x 1000 x 6/7 */
  arm_core_perf_cleanup();
  fake_perf_reset();
}

/* An outside holder (nvidia-powerd on GB200) leaves the general events 25% and the cycle event
 * 50%: running time falls below the solo-expected time by the outside share. */
static void test_sharing_outside_holder(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  make_tree("0x00000000410fd4f0\n");
  fake_perf_reset();
  c = cfg();
  assert(arm_core_perf_begin(2, &c) == 0);
  push_sharing_tick(500, 250);
  arm_core_perf_collect_cpu(1, &m);
  arm_core_perf_collect_cpu(1, &m);
  assert(m.pmu_enabled_ns == 9000ULL);
  assert(m.pmu_running_ns == 500ULL + 8 * 250); /* 2500 */
  assert(m.pmu_solo_ns == 7000ULL);
  assert(m.pmu_running_ns < m.pmu_solo_ns);
  /* Lifetimes accumulate across ticks. */
  push_sharing_tick(500, 250);
  arm_core_perf_collect_cpu(1, &m); /* the push re-seeds at 0: no time added */
  arm_core_perf_collect_cpu(1, &m);
  assert(m.pmu_enabled_ns == 18000ULL && m.pmu_running_ns == 5000ULL && m.pmu_solo_ns == 14000ULL);
  arm_core_perf_cleanup();
  fake_perf_reset();
}

/* A core without a verified MIDR row has no known general-counter count: report enabled and
 * running time, but never a guessed solo baseline. */
static void test_sharing_unknown_midr_no_solo(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  make_tree("0x00000000410fd400\n"); /* part 0xd40: no table row, only CYC / INS named */
  fake_perf_reset();
  c = cfg();
  assert(arm_core_perf_begin(1, &c) == 0);
  push_sharing_tick(1000, 400);
  arm_core_perf_collect_cpu(0, &m);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.have_times && !m.have_solo);
  assert(m.pmu_enabled_ns == 2000ULL && m.pmu_running_ns == 1400ULL && m.pmu_solo_ns == 0);
  arm_core_perf_cleanup();
  fake_perf_reset();
}

/* The first read only seeds: times stay 0 (the consumer sees no enabled time yet, so it
 * computes no share), and a CPU with no open event reports no times at all. */
static void test_sharing_no_enabled_time_publishes_nothing(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  make_tree("0x00000000410fd4f0\n");
  fake_perf_reset();
  c = cfg();
  assert(arm_core_perf_begin(1, &c) == 0);
  push_sharing_tick(1000, 750);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.have_times && m.pmu_enabled_ns == 0 && m.pmu_running_ns == 0 && m.pmu_solo_ns == 0);
  arm_core_perf_collect_cpu(3, &m); /* out of range */
  assert(!m.have_times && !m.have_solo && m.pmu_enabled_ns == 0);
  arm_core_perf_cleanup();
  arm_core_perf_collect_cpu(0, &m); /* not ready */
  assert(!m.have_times);
  fake_perf_reset();
}

static void test_unknown_midr_counts_only_cyc_ins(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;
  int e;

  make_tree("0x00000000410fd400\n");
  fake_perf_reset();
  c = cfg();
  assert(arm_core_perf_begin(1, &c) == 0);
  assert(g_fake_perf_n == 2);
  arm_core_perf_collect_cpu(0, &m);
  assert(m.have[ARM_CORE_CYC] && m.have[ARM_CORE_INS]);
  for (e = ARM_CORE_FP_DP; e < ARM_CORE_NEVENTS; e++)
    assert(!m.have[e] && m.life[e] == 0);
  arm_core_perf_cleanup();
  fake_perf_reset();
}

static void test_begin_fails_soft(void)
{
  struct arm_core_perf_cfg c;
  struct arm_core_counts m;

  /* Every open fails: -1, not ready, collect gives zeros. */
  make_tree("0x00000000410fd400\n");
  fake_perf_reset();
  fake_perf_fail(0x11, -1);
  c = cfg();
  fake_sysfs_write(g_root, "devices/armv8_pmuv3_0/events/inst_retired", "event=0x0011\n");
  assert(arm_core_perf_begin(2, &c) == -1);
  assert(!arm_core_perf_ready());
  arm_core_perf_collect_cpu(0, &m);
  assert(!m.have[ARM_CORE_CYC]);
  /* No PMU and bad CPU count. */
  fake_perf_reset();
  assert(arm_core_perf_begin(0, &c) == -1);
  fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_dev, sizeof(g_dev), "%s/devices", g_root);
  assert(arm_core_perf_begin(2, &c) == -1);
  arm_core_perf_cleanup(); /* idempotent */
  assert(arm_core_perf_event_name(ARM_CORE_FP_HP) != NULL);
  assert(strcmp(arm_core_perf_event_name(ARM_CORE_FP_HP), "fp_hp_spec") == 0);
  assert(arm_core_perf_event_name(ARM_CORE_NEVENTS) == NULL);
  assert(arm_core_perf_event_name(-1) == NULL);
}

int main(void)
{
  fake_sysfs_init(g_root, sizeof(g_root));
  test_read_midr_sysfs_then_cpuinfo();
  test_resolve_neoverse_v2_table();
  test_resolve_unknown_midr_only_named_events();
  test_begin_collect_scaled_lifetimes();
  test_event_sentinel_codes();
  test_one_event_failing_leaves_others();
  test_sharing_own_oversubscription_only();
  test_sharing_outside_holder();
  test_sharing_unknown_midr_no_solo();
  test_sharing_no_enabled_time_publishes_nothing();
  test_unknown_midr_counts_only_cyc_ins();
  test_begin_fails_soft();
  fake_sysfs_rm(g_root);
  printf("test_arm_core_perf passed\n");
  return 0;
}
