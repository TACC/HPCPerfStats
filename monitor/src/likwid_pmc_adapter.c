/*! \file likwid_pmc_adapter.c
 *  LIKWID HPMinit/perfmon bridge; maps fixc* counters to instr_retired/aperf/mperf.
 */

#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "trace.h"
#include "stats.h"
#include "msr_io.h"
#include "likwid_pmc_adapter.h"
#include "likwid_pmc_schema_map.h"
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
  if (strcmp(v, "0") == 0 || strcmp(v, "false") == 0 || strcmp(v, "false") == 0 ||
      strcmp(v, "no") == 0 || strcmp(v, "no") == 0)
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
  cpus = (int *)malloc((size_t)nr_threads * sizeof(*cpus));
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
  (void)nr_threads;
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
        (void)dup2(null_fd, STDERR_FILENO);
    }
  }
  g_group = perfmon_addEventSet(event_string);
  if (quiet && saved_stderr >= 0) {
    (void)dup2(saved_stderr, STDERR_FILENO);
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
        (void)dup2(null_fd, STDERR_FILENO);
    }
  }
  if (perfmon_setupCounters(g_group) < 0)
    goto err;
  if (perfmon_startCounters() < 0)
    goto err;
  if (quiet && saved_stderr >= 0) {
    (void)dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (quiet && null_fd >= 0)
    close(null_fd);
  return 0;
err:
  if (quiet && saved_stderr >= 0) {
    (void)dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (quiet && null_fd >= 0)
    close(null_fd);
  return -1;
#else
  (void)event_string;
  return -1;
#endif
}

static void set_counter_by_name(struct stats *stats, const char *counter_name,
                                unsigned long long value)
{
  int fixc;

  if (counter_name == NULL || stats == NULL)
    return;
  if (likwid_pmc_result_is_invalid(value))
    return;
  fixc = likwid_pmc_fixc_index(counter_name);
  if (fixc == 0)
    stats_set(stats, "instr_retired", value);
  else if (fixc == 1)
    stats_set(stats, "aperf", value);
  else if (fixc == 2)
    stats_set(stats, "mperf", value);
}

#ifdef HAVE_LIKWID
static void likwid_pmc_adapter_zero_fp_arith_stats(struct stats *stats)
{
  if (stats == NULL)
    return;
  stats_set(stats, "fp_arith_inst_retired_scalar_double", 0);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_double", 0);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_double", 0);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_double", 0);
  stats_set(stats, "fp_arith_inst_retired_scalar_single", 0);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_single", 0);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_single", 0);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_single", 0);
}

static void likwid_pmc_adapter_apply_one_event(struct stats *stats, const char *counter_name,
                                               const char *event_name, unsigned long long val,
                                               unsigned long long *inst_retired,
                                               unsigned long long *aperf, unsigned long long *mperf,
                                               int *have_inst, int *have_aperf, int *have_mperf,
                                               int *have_fixed0, int *have_fixed1, int *have_fixed2)
{
  char keybuf[128];
  const char *schema_key;
  int fixc;

  if (likwid_pmc_result_is_invalid(val))
    return;

  set_counter_by_name(stats, counter_name, val);
  fixc = likwid_pmc_fixc_index(counter_name);
  if (fixc == 0) {
    *inst_retired = val;
    *have_fixed0 = 1;
    *have_inst = 1;
  } else if (fixc == 1) {
    *aperf = val;
    *have_fixed1 = 1;
    *have_aperf = 1;
  } else if (fixc == 2) {
    *mperf = val;
    *have_fixed2 = 1;
    *have_mperf = 1;
  }
  if (event_name == NULL)
    return;
  schema_key = likwid_pmc_schema_key_from_event(event_name, keybuf, sizeof(keybuf));
  if (schema_key == NULL)
    return;
  stats_set(stats, schema_key, val);
  if (strcmp(schema_key, "instr_retired_any") == 0 ||
      strcmp(schema_key, "retired_instructions") == 0) {
    *inst_retired = val;
    *have_inst = 1;
  } else if (strcmp(schema_key, "cycles_unhalted_core") == 0) {
    *aperf = val;
    *have_aperf = 1;
  } else if (strcmp(schema_key, "cycles_unhalted_ref") == 0) {
    *mperf = val;
    *have_mperf = 1;
  }
}

static void likwid_pmc_adapter_publish_semantic_counters(
    struct stats *stats, unsigned long long inst, unsigned long long aperf_val,
    unsigned long long mperf_val, int have_inst, int have_aperf, int have_mperf, int have_fixed0,
    int have_fixed1, int have_fixed2)
{
  if (have_inst) {
    stats_set(stats, "instr_retired", inst);
    if (!have_fixed0)
      stats_set(stats, "instr_retired", inst);
  }
  if (have_aperf) {
    stats_set(stats, "aperf", aperf_val);
    if (!have_fixed1)
      stats_set(stats, "aperf", aperf_val);
  }
  if (have_mperf) {
    stats_set(stats, "mperf", mperf_val);
    if (!have_fixed2)
      stats_set(stats, "mperf", mperf_val);
  }
}
#endif /* HAVE_LIKWID */

static int read_msr_u64_cpu(int cpu, uint64_t reg, unsigned long long *val)
{
  char cpubuf[16];
  int fd;
  uint64_t tmp = 0;

  if (cpu < 0 || val == NULL)
    return -1;

  snprintf(cpubuf, sizeof(cpubuf), "%d", cpu);
  fd = msr_open_cpu(cpubuf, O_RDONLY);
  if (fd < 0)
    return -1;
  if (msr_read_u64(fd, (unsigned int)reg, &tmp) < 0) {
    close(fd);
    return -1;
  }
  close(fd);
  *val = (unsigned long long)tmp;
  return 0;
}

int likwid_pmc_adapter_read_cpu(struct stats *stats, int cpu, uint64_t *events, int nr_events,
                                int max_ctrs)
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

  (void)max_ctrs;
  (void)events;
  (void)nr_events;
  if (!g_initialized || g_group < 0 || stats == NULL || cpu < 0)
    return -1;
  likwid_pmc_adapter_zero_fp_arith_stats(stats);
  if (perfmon_readCounters() < 0)
    return -1;
  n_events = perfmon_getNumberOfEvents(g_group);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(g_group, i);
    const char *event_name = perfmon_getEventName(g_group, i);
    unsigned long long val = (unsigned long long)perfmon_getResult(g_group, i, cpu);

    likwid_pmc_adapter_apply_one_event(stats, counter_name, event_name, val, &inst_retired, &aperf,
                                       &mperf, &have_inst, &have_aperf, &have_mperf, &have_fixed0,
                                       &have_fixed1, &have_fixed2);
  }
  if (!have_inst && read_msr_u64_cpu(cpu, (uint64_t)MSR_PERF_INST_RETIRED, &inst_retired) == 0)
    have_inst = 1;
  if (!have_aperf && read_msr_u64_cpu(cpu, (uint64_t)MSR_PERF_APERF, &aperf) == 0)
    have_aperf = 1;
  if (!have_mperf && read_msr_u64_cpu(cpu, (uint64_t)MSR_PERF_MPERF, &mperf) == 0)
    have_mperf = 1;
  likwid_pmc_adapter_publish_semantic_counters(stats, inst_retired, aperf, mperf, have_inst,
                                               have_aperf, have_mperf, have_fixed0, have_fixed1,
                                               have_fixed2);
  return 0;
#else
  (void)stats;
  (void)cpu;
  (void)events;
  (void)nr_events;
  (void)max_ctrs;
  return -1;
#endif
}
