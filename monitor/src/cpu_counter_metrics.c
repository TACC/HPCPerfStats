#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "monitor_log.h"
#ifdef MONITOR_CPU_BACKEND_ARM
#include "cpu_counter_metrics_arm_state.h"
#include "cpu_counter_metrics_arm_publish.h"
#include "cpu_counter_metrics_arm_util.h"
#include "arm_core_perf.h"
#else
#include "likwid_pmc_adapter.h"
#include "likwid_arch_map.h"
#include "cpu_counter_metrics_likwid_begin.h"
#endif
#include "cpu_counter_metrics.h"

#ifdef MONITOR_CPU_BACKEND_ARM
/*
 * `arm` backend (aarch64 / Grace): util from /proc/stat jiffies over the daemon's own collect
 * interval, clock from cpufreq scaling_cur_freq (nominal chain fallback), and the measured core
 * counters (cycles, instructions, FP / SVE INT) from arm_core_perf (direct perf, scaled by
 * enabled/running; no LIKWID on aarch64). No DCGM: the embedded DCGM engine ran five 100-200 Hz timer
 * loops whatever the watch cadence (round 2 findings 7, 10, 18). Socket power is host_power_socket.
 */
unsigned long long *g_arm_ctr0 = NULL;
unsigned long long *g_arm_ctr1 = NULL;
unsigned long long *g_arm_ctr2 = NULL;
unsigned long long *g_arm_ctr3 = NULL;
unsigned long long *g_arm_ctr4 = NULL;
unsigned long long *g_arm_ctr5 = NULL;
unsigned long long *g_arm_aperf = NULL;
unsigned long long *g_arm_mperf = NULL;
unsigned long long *g_arm_dram_bytes = NULL;

static int g_arm_ready = 0;
static int g_arm_ncpus = 0;
static struct arm_cpu_jifs *g_arm_jifs_prev = NULL;
static struct arm_cpu_jifs *g_arm_jifs_cur = NULL;
static int g_arm_stat_seeded = 0;
static FILE *g_arm_proc_stat = NULL;
static int *g_arm_freq_fd = NULL;
static double *g_arm_nominal_khz = NULL;
static long long g_arm_mono_prev_us = 0;

/* Every per-CPU u64 accumulator, so alloc / free / reset stay one loop. */
static unsigned long long **const g_arm_ctrs[] = {&g_arm_ctr0,  &g_arm_ctr1,  &g_arm_ctr2,
                                                  &g_arm_ctr3,  &g_arm_ctr4,  &g_arm_ctr5,
                                                  &g_arm_aperf, &g_arm_mperf, &g_arm_dram_bytes};
#define ARM_NCTRS (sizeof(g_arm_ctrs) / sizeof(g_arm_ctrs[0]))

static int arm_proc_stat_read_cpus(struct arm_cpu_jifs *out, int ncpus)
{
  char *line = NULL;
  size_t line_size = 0;
  int any = 0;

  if (ncpus <= 0 || out == NULL)
    return -1;
  memset(out, 0, (size_t)ncpus * sizeof(*out));
  if (g_arm_proc_stat == NULL) {
    g_arm_proc_stat = fopen("/proc/stat", "re");
    if (g_arm_proc_stat == NULL)
      return -1;
  }
  rewind(g_arm_proc_stat);
  clearerr(g_arm_proc_stat);
  while (getline(&line, &line_size, g_arm_proc_stat) >= 0) {
    struct arm_cpu_jifs j;
    int idcpu = arm_proc_stat_parse_cpu_line(line, &j);

    if (idcpu < 0 || idcpu >= ncpus)
      continue;
    out[idcpu] = j;
    any = 1;
  }
  free(line);
  return any ? 0 : -1;
}

static void arm_backend_cleanup(void)
{
  size_t k;
  int i;

  arm_core_perf_cleanup();
  for (k = 0; k < ARM_NCTRS; k++) {
    free(*g_arm_ctrs[k]);
    *g_arm_ctrs[k] = NULL;
  }
  if (g_arm_freq_fd != NULL) {
    for (i = 0; i < g_arm_ncpus; i++) {
      if (g_arm_freq_fd[i] >= 0)
        close(g_arm_freq_fd[i]);
    }
  }
  free(g_arm_freq_fd);
  g_arm_freq_fd = NULL;
  free(g_arm_nominal_khz);
  g_arm_nominal_khz = NULL;
  free(g_arm_jifs_prev);
  g_arm_jifs_prev = NULL;
  free(g_arm_jifs_cur);
  g_arm_jifs_cur = NULL;
  if (g_arm_proc_stat != NULL) {
    fclose(g_arm_proc_stat);
    g_arm_proc_stat = NULL;
  }
  g_arm_ncpus = 0;
  g_arm_stat_seeded = 0;
  g_arm_mono_prev_us = 0;
  g_arm_ready = 0;
}

/* Allocate accumulators and open one scaling_cur_freq fd per CPU (startup / SIGHUP only). */
static int arm_backend_alloc(int ncpus)
{
  size_t n = (size_t)ncpus;
  size_t k;
  int i;

  for (k = 0; k < ARM_NCTRS; k++) {
    *g_arm_ctrs[k] = (unsigned long long *)calloc(n, sizeof(unsigned long long));
    if (*g_arm_ctrs[k] == NULL)
      return -1;
  }
  g_arm_jifs_prev = (struct arm_cpu_jifs *)calloc(n, sizeof(*g_arm_jifs_prev));
  g_arm_jifs_cur = (struct arm_cpu_jifs *)calloc(n, sizeof(*g_arm_jifs_cur));
  g_arm_freq_fd = (int *)malloc(n * sizeof(*g_arm_freq_fd));
  g_arm_nominal_khz = (double *)calloc(n, sizeof(*g_arm_nominal_khz));
  if (g_arm_jifs_prev == NULL || g_arm_jifs_cur == NULL || g_arm_freq_fd == NULL ||
      g_arm_nominal_khz == NULL)
    return -1;
  g_arm_ncpus = ncpus;
  for (i = 0; i < ncpus; i++) {
    g_arm_freq_fd[i] = arm_cpu_freq_open(ARM_CPU_SYSFS_ROOT, i);
    g_arm_nominal_khz[i] = arm_cpu_nominal_freq_khz(ARM_CPU_SYSFS_ROOT, i);
  }
  return 0;
}

static int arm_backend_begin(struct stats_type *type)
{
  if (g_arm_ready)
    return 0;
  arm_backend_cleanup();
  if (nr_cpus <= 0 || arm_backend_alloc(nr_cpus) != 0) {
    ERROR("arm CPU backend allocation failed (nr_cpus=%d)\n", nr_cpus);
    arm_backend_cleanup();
    type->st_enabled = 0;
    return 0;
  }
  /* Fail-soft: without core counters the type keeps /proc/stat util and util x freq cycles. */
  if (arm_core_perf_begin(nr_cpus, NULL) != 0)
    TRACE("arm core perf unavailable; /proc/stat util only\n");
  g_arm_ready = 1;
  type->st_enabled = 1;
  return 0;
}

/* Wall interval since the previous collect (0 on the first tick). */
static long long arm_collect_delta_us(void)
{
  struct timespec mono;
  long long now_us, delta = 0;

  if (clock_gettime(CLOCK_MONOTONIC, &mono) != 0)
    return 0;
  now_us = (long long)mono.tv_sec * 1000000LL + (long long)mono.tv_nsec / 1000LL;
  if (g_arm_mono_prev_us > 0 && now_us > g_arm_mono_prev_us)
    delta = now_us - g_arm_mono_prev_us;
  g_arm_mono_prev_us = now_us;
  return delta;
}

static void arm_collect_cpu(struct stats *stats, int i, int proc_stat_ok, long long delta_us)
{
  struct arm_cpu_sample sample;

  memset(&sample, 0, sizeof(sample));
  /* Unreadable /proc/stat this tick (or first tick): util credits 0; the next tick reseeds. */
  if (proc_stat_ok && g_arm_stat_seeded)
    arm_cpu_sample_from_jiffy_diff(&sample, &g_arm_jifs_cur[i], &g_arm_jifs_prev[i]);
  sample.clock_khz = arm_cpu_freq_read_khz(g_arm_freq_fd[i]);
  if (sample.clock_khz <= 0.0)
    sample.clock_khz = g_arm_nominal_khz[i];
  arm_accumulate_from_util_sample(i, &sample, delta_us);
  publish_arm_cpu_stats(stats, i);
  if (arm_core_perf_ready()) {
    struct arm_core_counts counts;

    arm_core_perf_collect_cpu(i, &counts);
    publish_arm_measured_stats(stats, &counts);
  }
}
#endif

static int cpu_counter_metrics_begin(struct stats_type *type)
{
#ifdef MONITOR_CPU_BACKEND_ARM
  return arm_backend_begin(type);
#else
  return likwid_backend_begin(type);
#endif
}

static void cpu_counter_metrics_collect(struct stats_type *type)
{
  int i;

#ifdef MONITOR_CPU_BACKEND_ARM
  int proc_stat_ok;
  long long delta_us;

  if (!g_arm_ready)
    return;
  proc_stat_ok = (arm_proc_stat_read_cpus(g_arm_jifs_cur, g_arm_ncpus) == 0);
  delta_us = arm_collect_delta_us();
  for (i = 0; i < g_arm_ncpus && i < nr_cpus; i++) {
    char cpu[80];
    struct stats *stats;

    snprintf(cpu, sizeof(cpu), "%d", i);
    stats = get_current_stats(type, cpu);
    if (stats == NULL)
      continue;
    arm_collect_cpu(stats, i, proc_stat_ok, delta_us);
  }
  if (proc_stat_ok) {
    memcpy(g_arm_jifs_prev, g_arm_jifs_cur, (size_t)g_arm_ncpus * sizeof(*g_arm_jifs_prev));
    g_arm_stat_seeded = 1;
  }
#else
  int skip_pmc_reads = 0;

  /* Re-program core LIKWID group once per tick after DF/RAPL setupCounters. */
  if (cpu_counter_metrics_likwid_ready() && likwid_pmc_adapter_prepare_collect() != 0)
    skip_pmc_reads = 1;

  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    struct stats *stats;
    snprintf(cpu, sizeof(cpu), "%d", i);
    stats = get_current_stats(type, cpu);
    if (stats == NULL)
      continue;
    /* LIKWID-only: no MSR fallback when setup failed or read_cpu fails. */
    if (!skip_pmc_reads && cpu_counter_metrics_likwid_ready()) {
      uint64_t ctls[8] = {0};
      (void)likwid_pmc_adapter_read_cpu(stats, i, ctls, 8, 8);
    }
  }
#endif
}

struct stats_type cpu_counter_metrics_stats_type = {
    .st_begin = &cpu_counter_metrics_begin,
    .st_collect = &cpu_counter_metrics_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(CPU_COUNTER_METRICS_KEYS),
#undef X
    .st_name = CPU_COUNTER_METRICS_ST_NAME,
};
