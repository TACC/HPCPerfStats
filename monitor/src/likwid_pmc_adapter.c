#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "trace.h"
#include "stats.h"
#include "likwid_pmc_adapter.h"
#include "amd64_pmc.h"

#ifdef HAVE_LIKWID
#include <likwid.h>
#endif

static int g_initialized = 0;
static int g_group = -1;

static int likwid_env_setup_quiet(void)
{
  const char *v = getenv("HPCPERFSTATS_LIKWID_SETUP_QUIET");
  if (v == NULL || *v == '\0')
    return 1;
  if (strcmp(v, "0") == 0 || strcmp(v, "false") == 0 || strcmp(v, "FALSE") == 0 ||
      strcmp(v, "no") == 0 || strcmp(v, "NO") == 0)
    return 0;
  return 1;
}

static int likwid_env_verbosity(void)
{
  const char *v = getenv("HPCPERFSTATS_LIKWID_VERBOSITY");
  if (v == NULL || *v == '\0')
    return 0;
  return atoi(v);
}

int likwid_pmc_adapter_init(int nr_threads)
{
#ifdef HAVE_LIKWID
  int rc = -1;
  int i = 0;
  int *cpus = NULL;
  if (nr_threads <= 0)
    return -1;
  cpus = (int *) malloc((size_t) nr_threads * sizeof(*cpus));
  if (cpus == NULL) {
    ERROR("cannot allocate LIKWID cpu map: %m\n");
    return -1;
  }
  for (i = 0; i < nr_threads; i++)
    cpus[i] = i;
  topology_init();
  numa_init();
  /* Default to quiet LIKWID logs; override with HPCPERFSTATS_LIKWID_VERBOSITY. */
  perfmon_setVerbosity(likwid_env_verbosity());
  /* Direct MSR access enables LIKWID power_init / RAPL (likwid_rapl). */
  HPMmode(ACCESSMODE_DIRECT);
  if (HPMinit() < 0) {
    ERROR("LIKWID HPMinit failed\n");
    goto out;
  }
  if (perfmon_init(nr_threads, cpus) < 0) {
    ERROR("LIKWID perfmon_init failed\n");
    goto out;
  }
  g_initialized = 1;
  rc = 0;
 out:
  free(cpus);
  return rc;
#else
  (void) nr_threads;
  return -1;
#endif
}

void likwid_pmc_adapter_finalize(void)
{
#ifdef HAVE_LIKWID
  if (g_initialized) {
    perfmon_finalize();
    HPMfinalize();
  }
#endif
  g_initialized = 0;
  g_group = -1;
}

int likwid_pmc_adapter_setup_events(const char *event_string)
{
#ifdef HAVE_LIKWID
  int saved_stderr = -1;
  int null_fd = -1;
  int quiet = 0;
  if (!g_initialized || event_string == NULL)
    return -1;
  quiet = likwid_env_setup_quiet();
  if (quiet) {
    saved_stderr = dup(STDERR_FILENO);
    if (saved_stderr >= 0) {
      null_fd = open("/dev/null", O_WRONLY);
      if (null_fd >= 0)
        (void) dup2(null_fd, STDERR_FILENO);
    }
  }
  g_group = perfmon_addEventSet(event_string);
  if (quiet && saved_stderr >= 0) {
    (void) dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
    saved_stderr = -1;
  }
  if (quiet && null_fd >= 0) {
    close(null_fd);
    null_fd = -1;
  }
  if (g_group < 0)
    return -1;
  if (quiet) {
    saved_stderr = dup(STDERR_FILENO);
    if (saved_stderr >= 0) {
      null_fd = open("/dev/null", O_WRONLY);
      if (null_fd >= 0)
        (void) dup2(null_fd, STDERR_FILENO);
    }
  }
  if (perfmon_setupCounters(g_group) < 0)
    goto err;
  if (perfmon_startCounters() < 0)
    goto err;
  if (quiet && saved_stderr >= 0) {
    (void) dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (quiet && null_fd >= 0)
    close(null_fd);
  return 0;
err:
  if (quiet && saved_stderr >= 0) {
    (void) dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (quiet && null_fd >= 0)
    close(null_fd);
  return -1;
#else
  (void) event_string;
  return -1;
#endif
}

static void set_counter_by_name(struct stats *stats, const char *counter_name,
                                unsigned long long value)
{
  if (counter_name == NULL || stats == NULL)
    return;
  if (strcmp(counter_name, "FIXC0") == 0)
    stats_set(stats, "FIXED_CTR0", value);
  else if (strcmp(counter_name, "FIXC1") == 0)
    stats_set(stats, "FIXED_CTR1", value);
  else if (strcmp(counter_name, "FIXC2") == 0)
    stats_set(stats, "FIXED_CTR2", value);
  else if (strcmp(counter_name, "PMC0") == 0)
    stats_set(stats, "CTR0", value);
  else if (strcmp(counter_name, "PMC1") == 0)
    stats_set(stats, "CTR1", value);
  else if (strcmp(counter_name, "PMC2") == 0)
    stats_set(stats, "CTR2", value);
  else if (strcmp(counter_name, "PMC3") == 0)
    stats_set(stats, "CTR3", value);
  else if (strcmp(counter_name, "PMC4") == 0)
    stats_set(stats, "CTR4", value);
  else if (strcmp(counter_name, "PMC5") == 0)
    stats_set(stats, "CTR5", value);
  else if (strcmp(counter_name, "PMC6") == 0)
    stats_set(stats, "CTR6", value);
  else if (strcmp(counter_name, "PMC7") == 0)
    stats_set(stats, "CTR7", value);
}

static int read_msr_u64_cpu(int cpu, uint64_t reg, unsigned long long *val)
{
  int fd;
  char msr_path[80];
  uint64_t tmp = 0;

  if (cpu < 0 || val == NULL)
    return -1;
  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%d/msr", cpu);
  fd = open(msr_path, O_RDONLY);
  if (fd < 0)
    return -1;
  if (pread(fd, &tmp, sizeof(tmp), reg) != (ssize_t) sizeof(tmp)) {
    close(fd);
    return -1;
  }
  close(fd);
  *val = (unsigned long long) tmp;
  return 0;
}

int likwid_pmc_adapter_read_cpu(struct stats *stats, int cpu, uint64_t *events,
                                int nr_events, int max_ctrs)
{
#ifdef HAVE_LIKWID
  int i = 0;
  int n_events = 0;
  unsigned long long inst_retired = 0;
  unsigned long long aperf = 0;
  unsigned long long mperf = 0;
  int have_fixed0 = 0;
  int have_fixed1 = 0;
  int have_fixed2 = 0;
  int have_inst = 0;
  int have_aperf = 0;
  int have_mperf = 0;
  (void) max_ctrs;
  if (!g_initialized || g_group < 0 || stats == NULL || cpu < 0)
    return -1;
  if (perfmon_readCounters() < 0)
    return -1;
  n_events = perfmon_getNumberOfEvents(g_group);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(g_group, i);
    const char *event_name = perfmon_getEventName(g_group, i);
    unsigned long long val = (unsigned long long) perfmon_getResult(g_group, i, cpu);
    set_counter_by_name(stats, counter_name, val);
    if (counter_name != NULL && strcmp(counter_name, "FIXC0") == 0) {
      inst_retired = val;
      have_fixed0 = 1;
      have_inst = 1;
    } else if (counter_name != NULL && strcmp(counter_name, "FIXC1") == 0) {
      aperf = val;
      have_fixed1 = 1;
      have_aperf = 1;
    } else if (counter_name != NULL && strcmp(counter_name, "FIXC2") == 0) {
      mperf = val;
      have_fixed2 = 1;
      have_mperf = 1;
    }
    if (event_name != NULL) {
      if (strcmp(event_name, "INSTR_RETIRED_ANY") == 0 ||
          strcmp(event_name, "RETIRED_INSTRUCTIONS") == 0) {
        inst_retired = val;
        have_inst = 1;
      } else if (strcmp(event_name, "CPU_CLK_UNHALTED_CORE") == 0) {
        aperf = val;
        have_aperf = 1;
      } else if (strcmp(event_name, "CPU_CLK_UNHALTED_REF") == 0) {
        mperf = val;
        have_mperf = 1;
      }
    }
  }

  /* Backfill cross-arch semantic counters from AMD MSRs if LIKWID group did not provide them. */
  if (!have_inst &&
      read_msr_u64_cpu(cpu, (uint64_t) MSR_PERF_INST_RETIRED, &inst_retired) == 0)
    have_inst = 1;
  if (!have_aperf &&
      read_msr_u64_cpu(cpu, (uint64_t) MSR_PERF_APERF, &aperf) == 0)
    have_aperf = 1;
  if (!have_mperf &&
      read_msr_u64_cpu(cpu, (uint64_t) MSR_PERF_MPERF, &mperf) == 0)
    have_mperf = 1;

  if (have_inst) {
    stats_set(stats, "INST_RETIRED", inst_retired);
    if (!have_fixed0)
      stats_set(stats, "FIXED_CTR0", inst_retired);
  }
  if (have_aperf) {
    stats_set(stats, "APERF", aperf);
    if (!have_fixed1)
      stats_set(stats, "FIXED_CTR1", aperf);
  }
  if (have_mperf) {
    stats_set(stats, "MPERF", mperf);
    if (!have_fixed2)
      stats_set(stats, "FIXED_CTR2", mperf);
  }
  for (i = 0; i < nr_events; i++) {
    char key[16];
    snprintf(key, sizeof(key), "CTL%d", i);
    stats_set(stats, key, events[i]);
  }
  return 0;
#else
  (void) stats;
  (void) cpu;
  (void) events;
  (void) nr_events;
  (void) max_ctrs;
  return -1;
#endif
}
