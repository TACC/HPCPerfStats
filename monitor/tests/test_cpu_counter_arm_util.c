/* Pure helpers of the `arm` host_cpu_hw backend: /proc/stat jiffy util and the cpufreq clock.
 * Built on every host (no DCGM, no libdcgm): the helpers are portable C. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "cpu_counter_metrics_arm_util.h"

static void test_clamp_percent(void)
{
  assert(arm_clamp_percent(-1.0) == 0.0);
  assert(arm_clamp_percent(0.0) == 0.0);
  assert(arm_clamp_percent(50.5) == 50.5);
  assert(arm_clamp_percent(100.0) == 100.0);
  assert(arm_clamp_percent(150.0) == 100.0);
}

static void test_sample_from_jiffy_diff(void)
{
  struct arm_cpu_jifs prev = {.u = 10, .sys = 10, .idle = 80};
  struct arm_cpu_jifs cur = {.u = 12, .sys = 12, .idle = 80};
  struct arm_cpu_sample s;

  memset(&s, 0, sizeof(s));
  arm_cpu_sample_from_jiffy_diff(&s, &cur, &prev);
  assert(s.util_total == 100.0);
  assert(s.util_user == 50.0);
  assert(s.util_sys == 50.0);
  assert(s.util_irq == 0.0);
  assert(s.util_nice == 0.0);

  arm_cpu_sample_from_jiffy_diff(NULL, &cur, &prev);
  arm_cpu_sample_from_jiffy_diff(&s, NULL, &prev);
  arm_cpu_sample_from_jiffy_diff(&s, &cur, NULL);

  /* Counters went backwards (CPU hotplug / wrap): sample untouched. */
  cur.u = 1;
  cur.sys = 1;
  cur.idle = 50;
  memset(&s, 0, sizeof(s));
  arm_cpu_sample_from_jiffy_diff(&s, &cur, &prev);
  assert(s.util_total == 0.0);

  /* No elapsed jiffies: sample untouched. */
  memset(&s, 0, sizeof(s));
  arm_cpu_sample_from_jiffy_diff(&s, &prev, &prev);
  assert(s.util_total == 0.0);
}

static void test_sample_from_jiffy_diff_irq_softirq_nice(void)
{
  struct arm_cpu_jifs prev = {.u = 0, .nice = 0, .sys = 0, .idle = 0, .irq = 0, .sft = 0};
  struct arm_cpu_jifs cur = {.u = 10, .nice = 10, .sys = 20, .idle = 40, .irq = 10, .sft = 10};
  struct arm_cpu_sample s;

  memset(&s, 0, sizeof(s));
  arm_cpu_sample_from_jiffy_diff(&s, &cur, &prev);
  assert(s.util_total == 60.0);
  assert(s.util_user == 20.0); /* user + nice */
  assert(s.util_sys == 20.0);
  assert(s.util_irq == 20.0); /* irq + softirq */
  assert(s.util_nice == 0.0);
}

static void test_proc_stat_parse_cpu_line(void)
{
  struct arm_cpu_jifs j;

  assert(arm_proc_stat_parse_cpu_line("cpu7 1 2 3 4 5 6 7 8 9 10\n", &j) == 7);
  assert(j.u == 1 && j.nice == 2 && j.sys == 3 && j.idle == 4 && j.gn == 10);
  assert(arm_proc_stat_parse_cpu_line("cpu12 5 0 5 90\n", &j) == 12);
  assert(j.idle == 90 && j.iow == 0);
  /* Aggregate line, other lines, short lines and NULL are rejected. */
  assert(arm_proc_stat_parse_cpu_line("cpu  1 2 3 4 5 6 7 8 9 10\n", &j) == -1);
  assert(arm_proc_stat_parse_cpu_line("intr 1 2 3\n", &j) == -1);
  assert(arm_proc_stat_parse_cpu_line("cpu3 1 2\n", &j) == -1);
  assert(arm_proc_stat_parse_cpu_line(NULL, &j) == -1);
  assert(arm_proc_stat_parse_cpu_line("cpu0 1 2 3 4\n", NULL) == -1);
}

static void write_file(const char *dir, const char *name, const char *text)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", dir, name);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(text, f);
  fclose(f);
}

static void make_cpufreq_dir(const char *root, int cpu, char *out, size_t out_sz)
{
  char path[512];

  snprintf(path, sizeof(path), "%s/cpu%d", root, cpu);
  assert(mkdir(path, 0700) == 0 || errno == EEXIST);
  snprintf(out, out_sz, "%s/cpu%d/cpufreq", root, cpu);
  assert(mkdir(out, 0700) == 0 || errno == EEXIST);
}

static void test_cpu_freq_scaling_cur_and_nominal_chain(void)
{
  char root[] = "/tmp/test_arm_cpufreq_XXXXXX";
  char d0[512], d1[512], d2[512];
  int fd;

  assert(mkdtemp(root) != NULL);
  make_cpufreq_dir(root, 0, d0, sizeof(d0));
  make_cpufreq_dir(root, 1, d1, sizeof(d1));
  make_cpufreq_dir(root, 2, d2, sizeof(d2));
  /* cpu0: scaling_cur_freq present; cpuinfo_cur_freq holds the bogus CPPC value. */
  write_file(d0, "scaling_cur_freq", "3357000\n");
  write_file(d0, "cpuinfo_cur_freq", "6282000\n");
  write_file(d0, "cpuinfo_max_freq", "3375000\n");
  /* cpu1: no scaling_cur_freq, base_frequency wins the nominal chain. */
  write_file(d1, "base_frequency", "3100000\n");
  write_file(d1, "cpuinfo_max_freq", "3375000\n");
  /* cpu2: empty cpufreq dir; nominal falls back to cpu0's chain. */

  fd = arm_cpu_freq_open(root, 0);
  assert(fd >= 0);
  assert(arm_cpu_freq_read_khz(fd) == 3357000.0);
  /* pread re-reads the live value without reopening. */
  write_file(d0, "scaling_cur_freq", "1120000\n");
  assert(arm_cpu_freq_read_khz(fd) == 1120000.0);
  close(fd);

  assert(arm_cpu_freq_open(root, 1) == -1);
  assert(arm_cpu_freq_open(NULL, 0) == -1);
  assert(arm_cpu_freq_open(root, -1) == -1);
  assert(arm_cpu_freq_read_khz(-1) == 0.0);

  assert(arm_cpu_nominal_freq_khz(root, 0) == 3375000.0);
  assert(arm_cpu_nominal_freq_khz(root, 1) == 3100000.0);
  assert(arm_cpu_nominal_freq_khz(root, 2) == 3375000.0);
  assert(arm_cpu_nominal_freq_khz(NULL, 0) == 0.0);

  {
    char cmd[600];

    snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
    assert(system(cmd) == 0);
  }
}

static void test_cpu_freq_garbage_reads_zero(void)
{
  char root[] = "/tmp/test_arm_cpufreq_bad_XXXXXX";
  char d0[512];
  int fd;

  assert(mkdtemp(root) != NULL);
  make_cpufreq_dir(root, 0, d0, sizeof(d0));
  write_file(d0, "scaling_cur_freq", "n/a\n");
  fd = arm_cpu_freq_open(root, 0);
  assert(fd >= 0);
  assert(arm_cpu_freq_read_khz(fd) == 0.0);
  close(fd);
  write_file(d0, "scaling_cur_freq", "-5\n");
  fd = arm_cpu_freq_open(root, 0);
  assert(arm_cpu_freq_read_khz(fd) == 0.0);
  close(fd);
  /* No chain file readable on cpu0: nominal 0. */
  assert(arm_cpu_nominal_freq_khz(root, 0) == 0.0);
  {
    char cmd[600];

    snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
    assert(system(cmd) == 0);
  }
}

int main(void)
{
  test_clamp_percent();
  test_sample_from_jiffy_diff();
  test_sample_from_jiffy_diff_irq_softirq_nice();
  test_proc_stat_parse_cpu_line();
  test_cpu_freq_scaling_cur_and_nominal_chain();
  test_cpu_freq_garbage_reads_zero();
  printf("test_cpu_counter_arm_util passed\n");
  return 0;
}
