/* arm_core_perf: Grace core event resolution (O12) and scaled per-CPU lifetimes.
 *
 * Finding #8 (c122-018, Neoverse V2 MIDR 0x410fd4f0): the kernel names only cpu_cycles (0x11)
 * and inst_retired (0x08); the seven FP / SVE INT events come from the MIDR raw-code table.
 * Regression for the 2026-11-13 "always zero" finding: the Grace FP keys were never programmed. */
#include <assert.h>
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
  test_one_event_failing_leaves_others();
  test_unknown_midr_counts_only_cyc_ins();
  test_begin_fails_soft();
  fake_sysfs_rm(g_root);
  printf("test_arm_core_perf passed\n");
  return 0;
}
