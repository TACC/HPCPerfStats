/*! \file likwid_rapl_pwr.c
 *  RAPL energy via LIKWID PWR* perfmon (perf power PMU under ACCESSMODE_PERF).
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "likwid_rapl_pwr.h"
#include "monitor_log.h"
#include "trace.h"

#ifdef HAVE_LIKWID
#include <likwid.h>
#endif

/* Prefer full Intel set; callers may fall back by retrying a shorter string. */
static const char k_intel_pwr_full[] =
    "PWR_PKG_ENERGY:PWR0,PWR_PP0_ENERGY:PWR1,PWR_PP1_ENERGY:PWR2,PWR_DRAM_ENERGY:PWR3";
static const char k_intel_pwr_no_pp1[] =
    "PWR_PKG_ENERGY:PWR0,PWR_PP0_ENERGY:PWR1,PWR_DRAM_ENERGY:PWR3";
static const char k_intel_pwr_pkg_dram[] = "PWR_PKG_ENERGY:PWR0,PWR_DRAM_ENERGY:PWR3";
static const char k_intel_pwr_pkg[] = "PWR_PKG_ENERGY:PWR0";
static const char k_amd_pwr_pkg[] = "PWR_PKG_ENERGY:PWR0";

static int g_pwr_group = -1;
static int g_pwr_ready;
static int g_pwr_amd_path;

const char *likwid_rapl_pwr_intel_eventset(void)
{
  return k_intel_pwr_full;
}

const char *likwid_rapl_pwr_amd_eventset(void)
{
  return k_amd_pwr_pkg;
}

const char *likwid_rapl_pwr_schema_key_from_event(const char *event_name, int amd_path)
{
  if (event_name == NULL || event_name[0] == '\0')
    return NULL;
  if (strstr(event_name, "PKG") != NULL)
    return "pkg_energy";
  if (strstr(event_name, "DRAM") != NULL)
    return "dram_energy";
  if (strstr(event_name, "PP1") != NULL)
    return "pp1_energy";
  if (strstr(event_name, "PP0") != NULL)
    return amd_path ? "core_energy" : "pp0_energy";
  return NULL;
}

unsigned long long likwid_rapl_joules_to_mj(double joules)
{
  if (joules < 0.0)
    return 0;
  return (unsigned long long)(joules * 1000.0 + 0.5);
}

#ifdef HAVE_LIKWID
static int likwid_rapl_pwr_try_eventset(const char *events, int *group_out)
{
  int group;
  int saved_stderr = -1;
  int null_fd = -1;

  if (events == NULL || events[0] == '\0' || group_out == NULL)
    return -1;

  saved_stderr = dup(STDERR_FILENO);
  if (saved_stderr >= 0) {
    null_fd = open("/dev/null", O_WRONLY);
    if (null_fd >= 0)
      (void)dup2(null_fd, STDERR_FILENO);
  }

  errno = 0;
  group = perfmon_addEventSet(events);
  if (saved_stderr >= 0) {
    (void)dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (null_fd >= 0)
    close(null_fd);

  if (group < 0)
    group = perfmon_addEventSet(events);
  if (group < 0)
    return -1;
  if (perfmon_setupCounters(group) < 0)
    return -1;
  (void)perfmon_startCounters();
  *group_out = group;
  return 0;
}
#endif

int likwid_rapl_pwr_begin(int amd_path)
{
#ifdef HAVE_LIKWID
  int group = -1;
  const char *chosen = NULL;

  g_pwr_group = -1;
  g_pwr_ready = 0;
  g_pwr_amd_path = amd_path ? 1 : 0;

  if (amd_path) {
    if (likwid_rapl_pwr_try_eventset(k_amd_pwr_pkg, &group) == 0)
      chosen = k_amd_pwr_pkg;
  } else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_full, &group) == 0) {
    chosen = k_intel_pwr_full;
  } else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_no_pp1, &group) == 0) {
    chosen = k_intel_pwr_no_pp1;
  } else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_pkg_dram, &group) == 0) {
    chosen = k_intel_pwr_pkg_dram;
  } else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_pkg, &group) == 0) {
    chosen = k_intel_pwr_pkg;
  }

  if (group < 0 || chosen == NULL) {
    monitor_log_error("likwid_rapl_pwr: perfmon_addEventSet failed for PWR RAPL events\n");
    return -1;
  }

  g_pwr_group = group;
  g_pwr_ready = 1;
  monitor_log_info("likwid_rapl_pwr: enabled eventset `%s`\n", chosen);
  return 0;
#else
  (void)amd_path;
  return -1;
#endif
}

int likwid_rapl_pwr_ready(void)
{
  return g_pwr_ready;
}

int likwid_rapl_pwr_collect_socket_mj(int cpu_id, unsigned int socket_id,
                                      unsigned long long *pkg_mj, unsigned long long *core_mj,
                                      unsigned long long *dram_mj, int *has_pkg, int *has_core,
                                      int *has_dram, unsigned long long *pp1_mj, int *has_pp1)
{
#ifdef HAVE_LIKWID
  int n_events;
  int i;

  (void)socket_id;
  if (pkg_mj == NULL || core_mj == NULL || dram_mj == NULL || has_pkg == NULL || has_core == NULL ||
      has_dram == NULL)
    return -1;
  *pkg_mj = *core_mj = *dram_mj = 0;
  *has_pkg = *has_core = *has_dram = 0;
  if (pp1_mj != NULL && has_pp1 != NULL) {
    *pp1_mj = 0;
    *has_pp1 = 0;
  }
  if (!g_pwr_ready || g_pwr_group < 0 || cpu_id < 0)
    return -1;
  if (perfmon_readGroupCounters(g_pwr_group) < 0)
    return -1;
  n_events = perfmon_getNumberOfEvents(g_pwr_group);
  for (i = 0; i < n_events; i++) {
    const char *event_name = perfmon_getEventName(g_pwr_group, i);
    const char *key;
    double raw;
    unsigned long long mj = 0;

    key = likwid_rapl_pwr_schema_key_from_event(event_name, g_pwr_amd_path);
    if (key == NULL)
      continue;
    raw = perfmon_getResult(g_pwr_group, i, cpu_id);
    if (raw < 0.0)
      continue;
    /* PWR results are Joules; schema keys are millijoules. */
    mj = likwid_rapl_joules_to_mj(raw);
    if (strcmp(key, "pkg_energy") == 0) {
      *pkg_mj = mj;
      *has_pkg = 1;
    } else if (strcmp(key, "pp0_energy") == 0 || strcmp(key, "core_energy") == 0) {
      *core_mj = mj;
      *has_core = 1;
    } else if (strcmp(key, "pp1_energy") == 0) {
      if (pp1_mj != NULL && has_pp1 != NULL) {
        *pp1_mj = mj;
        *has_pp1 = 1;
      }
    } else if (strcmp(key, "dram_energy") == 0) {
      *dram_mj = mj;
      *has_dram = 1;
    }
  }
  if (*has_pkg || *has_core || *has_dram || (has_pp1 != NULL && *has_pp1))
    return 0;
  TRACE("likwid_rapl_pwr: no energy results for cpu_id=%d\n", cpu_id);
  return -1;
#else
  (void)cpu_id;
  (void)socket_id;
  (void)pkg_mj;
  (void)core_mj;
  (void)dram_mj;
  (void)has_pkg;
  (void)has_core;
  (void)has_dram;
  (void)pp1_mj;
  (void)has_pp1;
  return -1;
#endif
}
