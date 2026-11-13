/* arm_perf_util: sysfs PMU parse and the enabled/running scaled read.
 *
 * Regression for finding #5 (c122-018, 2026-11-13): nvidia-powerd holds most Grace core PMCs, so
 * events run ~25% of the time; LIKWID read bare counts without time_enabled/time_running and the
 * monitor published ~4x low instr/cycles. Every read must scale by d_enabled / d_running. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "arm_perf_util.h"
#include "test_fake_perf.h"

static char g_root[512];

static void sample(struct arm_perf_prev *p, uint64_t v, uint64_t en, uint64_t run, double *d)
{
  const uint64_t s[3] = {v, en, run};

  arm_perf_scale_sample(p, s, d);
}

static void test_scale_first_sample_seeds(void)
{
  struct arm_perf_prev p;
  double d = -1.0;

  memset(&p, 0, sizeof(p));
  sample(&p, 1000, 500, 500, &d);
  assert(d == 0.0);
  assert(p.seeded && p.value == 1000 && p.enabled == 500 && p.running == 500);
}

static void test_scale_multiplexed_delta(void)
{
  struct arm_perf_prev p;
  double d;

  memset(&p, 0, sizeof(p));
  sample(&p, 0, 0, 0, &d);
  /* Counted 1000 events while scheduled 25% of the enabled time: 4000 estimated. */
  sample(&p, 1000, 400, 100, &d);
  assert(d == 4000.0);
  /* enabled == running (no contention): the raw delta. */
  sample(&p, 1500, 500, 200, &d);
  assert(d == 500.0);
}

static void test_scale_not_running_adds_nothing(void)
{
  struct arm_perf_prev p;
  double d;

  memset(&p, 0, sizeof(p));
  sample(&p, 10, 10, 10, &d);
  sample(&p, 10, 50, 10, &d); /* enabled but never scheduled this tick */
  assert(d == 0.0);
  assert(p.enabled == 50);
  sample(&p, 30, 60, 20, &d); /* next tick scales from the updated baseline */
  assert(d == 20.0);
}

static void test_scale_counter_reset_reseeds(void)
{
  struct arm_perf_prev p;
  double d;

  memset(&p, 0, sizeof(p));
  sample(&p, 5000, 100, 100, &d);
  sample(&p, 40, 110, 110, &d); /* value went down: reseed, add nothing */
  assert(d == 0.0);
  assert(p.value == 40);
  sample(&p, 140, 120, 120, &d);
  assert(d == 100.0);
  sample(&p, 150, 10, 10, &d); /* times went down (counter recreated): reseed */
  assert(d == 0.0);
}

static void test_scale_null_inputs(void)
{
  struct arm_perf_prev p;
  const uint64_t s[3] = {1, 1, 1};
  double d = 7.0;

  memset(&p, 0, sizeof(p));
  arm_perf_scale_sample(NULL, s, &d);
  assert(d == 0.0);
  arm_perf_scale_sample(&p, NULL, &d);
  arm_perf_scale_sample(&p, s, NULL);
  assert(!p.seeded);
}

static void test_read_scaled_from_fd(void)
{
  struct arm_perf_prev p;
  double d = -1.0;
  int fds[2];
  uint64_t rec[3] = {100, 1000, 1000};
  uint64_t rec2[3] = {300, 2000, 1500};

  memset(&p, 0, sizeof(p));
  assert(pipe(fds) == 0);
  assert(write(fds[1], rec, sizeof(rec)) == (ssize_t)sizeof(rec));
  assert(write(fds[1], rec2, sizeof(rec2)) == (ssize_t)sizeof(rec2));
  assert(arm_perf_read_scaled(fds[0], &p, &d) == 0 && d == 0.0);
  assert(arm_perf_read_scaled(fds[0], &p, &d) == 0 && d == 400.0); /* 200 x 1000 / 500 */
  /* Short read (8 of 24 bytes): error, prev unchanged. */
  assert(write(fds[1], rec, 8) == 8);
  close(fds[1]);
  assert(arm_perf_read_scaled(fds[0], &p, &d) == -1 && d == 0.0);
  assert(p.value == 300);
  close(fds[0]);
  assert(arm_perf_read_scaled(-1, &p, &d) == -1);
  assert(arm_perf_read_scaled(fds[0], NULL, &d) == -1);
}

static void test_attr_raw_scaled_not_pinned(void)
{
  struct perf_event_attr a;

  arm_perf_attr_raw(&a, 8, 0x801C);
  assert(a.type == 8 && a.config == 0x801C && a.size == sizeof(a));
  assert(a.read_format == (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING));
  assert(a.pinned == 0 && a.exclusive == 0 && a.disabled == 0);
  arm_perf_attr_raw(NULL, 1, 1); /* no crash */
}

static void test_attr_from_sysfs_scf_encoding(void)
{
  struct perf_event_attr a;
  char pmu[600];

  /* Finding #6: nvidia_scf_pmu_0 type=25, cmem_rd_data=event=0x1a5. */
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/type", "25\n");
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/format/event", "config:0-31\n");
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/format/umask", "config:32-39\n");
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/format/filter", "config1:0-15\n");
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/events/cmem_rd_data", "event=0x1a5\n");
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/events/multi", "event=0x3,umask=0x2,filter=7\n");
  fake_sysfs_write(g_root, "nvidia_scf_pmu_0/events/badterm", "nosuch=0x1\n");
  snprintf(pmu, sizeof(pmu), "%s/nvidia_scf_pmu_0", g_root);
  memset(&a, 0xff, sizeof(a));
  assert(arm_perf_attr_from_sysfs(pmu, "cmem_rd_data", &a) == 0);
  assert(a.type == 25 && a.config == 0x1a5 && a.config1 == 0);
  assert(a.read_format == (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING));
  assert(a.pinned == 0 && a.exclusive == 0);
  assert(arm_perf_attr_from_sysfs(pmu, "multi", &a) == 0);
  assert(a.config == (0x3ULL | (0x2ULL << 32)) && a.config1 == 7);
  /* Missing event, unknown format term, missing PMU: errors. */
  assert(arm_perf_attr_from_sysfs(pmu, "cmem_nosuch", &a) == -1);
  assert(arm_perf_attr_from_sysfs(pmu, "badterm", &a) == -1);
  assert(arm_perf_attr_from_sysfs("/nonexistent/hps", "cmem_rd_data", &a) == -1);
  assert(arm_perf_attr_from_sysfs(NULL, "cmem_rd_data", &a) == -1);
  assert(arm_perf_attr_from_sysfs(pmu, NULL, &a) == -1);
}

static void test_pmu_type(void)
{
  char pmu[600];
  uint32_t t = 0;

  fake_sysfs_write(g_root, "nvidia_scf_pmu_1/type", "15\n");
  fake_sysfs_write(g_root, "bad_pmu/type", "x\n");
  snprintf(pmu, sizeof(pmu), "%s/nvidia_scf_pmu_1", g_root);
  assert(arm_perf_pmu_type(pmu, &t) == 0 && t == 15);
  snprintf(pmu, sizeof(pmu), "%s/bad_pmu", g_root);
  assert(arm_perf_pmu_type(pmu, &t) == -1);
  assert(arm_perf_pmu_type(NULL, &t) == -1);
  assert(arm_perf_pmu_type(pmu, NULL) == -1);
}

static void test_cpumask_first(void)
{
  char path[600];

  fake_sysfs_write(g_root, "m1", "72\n");
  fake_sysfs_write(g_root, "m2", "0-3,8\n");
  fake_sysfs_write(g_root, "m3", "garbage\n");
  snprintf(path, sizeof(path), "%s/m1", g_root);
  assert(arm_perf_cpumask_first(path) == 72);
  snprintf(path, sizeof(path), "%s/m2", g_root);
  assert(arm_perf_cpumask_first(path) == 0);
  snprintf(path, sizeof(path), "%s/m3", g_root);
  assert(arm_perf_cpumask_first(path) == -1);
  snprintf(path, sizeof(path), "%s/missing", g_root);
  assert(arm_perf_cpumask_first(path) == -1);
  assert(arm_perf_cpumask_first(NULL) == -1);
}

static void test_open_cpu_rejects_bad_input(void)
{
  struct perf_event_attr a;

  arm_perf_attr_raw(&a, 1, 0);
  errno = 0;
  assert(arm_perf_open_cpu(NULL, 0) == -1 && errno == EINVAL);
  errno = 0;
  assert(arm_perf_open_cpu(&a, -1) == -1 && errno == EINVAL);
}

int main(void)
{
  fake_sysfs_init(g_root, sizeof(g_root));
  test_scale_first_sample_seeds();
  test_scale_multiplexed_delta();
  test_scale_not_running_adds_nothing();
  test_scale_counter_reset_reseeds();
  test_scale_null_inputs();
  test_read_scaled_from_fd();
  test_attr_raw_scaled_not_pinned();
  test_attr_from_sysfs_scf_encoding();
  test_pmu_type();
  test_cpumask_first();
  test_open_cpu_rejects_bad_input();
  fake_sysfs_rm(g_root);
  printf("test_arm_perf_util passed\n");
  return 0;
}
