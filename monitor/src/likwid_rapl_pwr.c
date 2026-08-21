/*! \file likwid_rapl_pwr.c
 *  RAPL energy via LIKWID PWR* perfmon (perf power PMU) with powercap fallback.
 */

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "likwid_rapl_pwr.h"
#include "monitor_log.h"
#include "rapl_powercap.h"
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
static int g_powercap_ok;
static int g_pwr_amd_path;

const char *likwid_rapl_pwr_intel_eventset(void)
{
  return k_intel_pwr_full;
}

const char *likwid_rapl_pwr_intel_eventset_for_domains(int has_pkg, int has_dram, int has_pp0,
                                                       int has_pp1)
{
  if (has_pkg && has_pp0 && has_pp1 && has_dram)
    return k_intel_pwr_full;
  if (has_pkg && has_pp0 && has_dram)
    return k_intel_pwr_no_pp1;
  if (has_pkg && has_dram)
    return k_intel_pwr_pkg_dram;
  if (has_pkg)
    return k_intel_pwr_pkg;
  return NULL;
}

void likwid_rapl_pwr_probe_power_domains(int *has_pkg, int *has_dram, int *has_pp0, int *has_pp1)
{
  if (has_pkg != NULL)
    *has_pkg = 0;
  if (has_dram != NULL)
    *has_dram = 0;
  if (has_pp0 != NULL)
    *has_pp0 = 0;
  if (has_pp1 != NULL)
    *has_pp1 = 0;

  if (access("/sys/bus/event_source/devices/power/events/energy-pkg", F_OK) == 0 && has_pkg != NULL)
    *has_pkg = 1;
  if (access("/sys/bus/event_source/devices/power/events/energy-ram", F_OK) == 0 &&
      has_dram != NULL)
    *has_dram = 1;
  if (access("/sys/bus/event_source/devices/power/events/energy-cores", F_OK) == 0 &&
      has_pp0 != NULL)
    *has_pp0 = 1;
  if (access("/sys/bus/event_source/devices/power/events/energy-gpu", F_OK) == 0 && has_pp1 != NULL)
    *has_pp1 = 1;
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
  if (!(joules > 0.0) || !isfinite(joules))
    return 0;
  return (unsigned long long)(joules * 1000.0 + 0.5);
}

int likwid_rapl_pwr_result_usable(double joules)
{
  return isfinite(joules) && joules > 0.0;
}

#ifdef HAVE_LIKWID
static void likwid_rapl_pwr_restore_stderr(int saved_stderr, int null_fd)
{
  if (saved_stderr >= 0) {
    (void)dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (null_fd >= 0)
    close(null_fd);
}

static int likwid_rapl_pwr_try_eventset(const char *events, int *group_out)
{
  int group;
  int saved_stderr = -1;
  int null_fd = -1;
  int quiet = 0;

  if (events == NULL || events[0] == '\0' || group_out == NULL)
    return -1;

  saved_stderr = dup(STDERR_FILENO);
  if (saved_stderr >= 0) {
    null_fd = open("/dev/null", O_WRONLY);
    if (null_fd >= 0) {
      (void)dup2(null_fd, STDERR_FILENO);
      quiet = 1;
    }
  }

  errno = 0;
  group = perfmon_addEventSet(events);
  if (group < 0 && quiet) {
    likwid_rapl_pwr_restore_stderr(saved_stderr, null_fd);
    saved_stderr = -1;
    null_fd = -1;
    quiet = 0;
    group = perfmon_addEventSet(events);
  }
  if (group < 0) {
    if (quiet)
      likwid_rapl_pwr_restore_stderr(saved_stderr, null_fd);
    return -1;
  }
  if (perfmon_setupCounters(group) < 0) {
    if (quiet)
      likwid_rapl_pwr_restore_stderr(saved_stderr, null_fd);
    return -1;
  }
  (void)perfmon_startCounters();
  if (quiet)
    likwid_rapl_pwr_restore_stderr(saved_stderr, null_fd);
  *group_out = group;
  return 0;
}

/*
 * LIKWID POWER events are programmed only on the socket-lock CPU. Prefer
 * cpu_id, then take the max usable result across threads.
 */
static double likwid_rapl_pwr_best_result(int group, int event_id, int cpu_id)
{
  double best = perfmon_getResult(group, event_id, cpu_id);
  int n_threads;
  int t;

  if (likwid_rapl_pwr_result_usable(best))
    return best;
  n_threads = perfmon_getNumberOfThreads();
  for (t = 0; t < n_threads; t++) {
    double raw = perfmon_getResult(group, event_id, t);

    if (likwid_rapl_pwr_result_usable(raw) && (!isfinite(best) || raw > best))
      best = raw;
  }
  return best;
}

static void likwid_rapl_pwr_seed_energy_units(void)
{
  double pkg_u;
  PowerInfo_t pi;

  /* LIKWID calculateResult multiplies POWER by power_getEnergyUnit; under PERF
   * power_init skips MSR units, but getEnergyUnit lazy-loads sysfs scales. */
  pkg_u = power_getEnergyUnit(PKG);
  pi = get_powerInfo();
  if (pi != NULL && pkg_u <= 0.0 && rapl_powercap_available()) {
    /* Keep PWR path from permanently zeroing if sysfs scale path is missing. */
    monitor_log_warn("likwid_rapl_pwr: power_getEnergyUnit(PKG)=%.3g; will prefer powercap "
                     "energy_uj if PWR results stay flat\n",
                     pkg_u);
  } else if (pkg_u > 0.0) {
    TRACE("likwid_rapl_pwr: energy unit PKG=%g\n", pkg_u);
  }
  (void)power_getEnergyUnit(PP0);
  (void)power_getEnergyUnit(DRAM);
  (void)power_getEnergyUnit(PP1);
}
#endif

int likwid_rapl_pwr_begin(int amd_path)
{
#ifdef HAVE_LIKWID
  int group = -1;
  const char *chosen = NULL;
  int has_pkg = 0;
  int has_dram = 0;
  int has_pp0 = 0;
  int has_pp1 = 0;
  const char *probed = NULL;

  g_pwr_group = -1;
  g_pwr_ready = 0;
  g_powercap_ok = 0;
  g_pwr_amd_path = amd_path ? 1 : 0;

  if (amd_path) {
    if (likwid_rapl_pwr_try_eventset(k_amd_pwr_pkg, &group) == 0)
      chosen = k_amd_pwr_pkg;
  } else {
    likwid_rapl_pwr_probe_power_domains(&has_pkg, &has_dram, &has_pp0, &has_pp1);
    probed = likwid_rapl_pwr_intel_eventset_for_domains(has_pkg, has_dram, has_pp0, has_pp1);
    if (probed != NULL && likwid_rapl_pwr_try_eventset(probed, &group) == 0) {
      chosen = probed;
    } else {
      /* No/partial sysfs: prefer pkg+dram then pkg — never try full first (PP spam). */
      if (likwid_rapl_pwr_try_eventset(k_intel_pwr_pkg_dram, &group) == 0)
        chosen = k_intel_pwr_pkg_dram;
      else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_pkg, &group) == 0)
        chosen = k_intel_pwr_pkg;
      else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_no_pp1, &group) == 0)
        chosen = k_intel_pwr_no_pp1;
      else if (likwid_rapl_pwr_try_eventset(k_intel_pwr_full, &group) == 0)
        chosen = k_intel_pwr_full;
    }
  }

  g_powercap_ok = rapl_powercap_available() ? 1 : 0;

  if (group >= 0 && chosen != NULL) {
    g_pwr_group = group;
    g_pwr_ready = 1;
    likwid_rapl_pwr_seed_energy_units();
    monitor_log_info("likwid_rapl_pwr: enabled eventset `%s`\n", chosen);
  } else {
    monitor_log_error("likwid_rapl_pwr: perfmon_addEventSet failed for PWR RAPL events\n");
  }

  if (g_powercap_ok)
    monitor_log_info("likwid_rapl_pwr: powercap energy_uj available (PERF fallback)\n");

  if (!g_pwr_ready && !g_powercap_ok)
    return -1;
  return 0;
#else
  (void)amd_path;
  g_powercap_ok = rapl_powercap_available() ? 1 : 0;
  return g_powercap_ok ? 0 : -1;
#endif
}

int likwid_rapl_pwr_ready(void)
{
  return g_pwr_ready || g_powercap_ok;
}

int likwid_rapl_pwr_collect_socket_mj(int cpu_id, unsigned int socket_id,
                                      unsigned long long *pkg_mj, unsigned long long *core_mj,
                                      unsigned long long *dram_mj, int *has_pkg, int *has_core,
                                      int *has_dram, unsigned long long *pp1_mj, int *has_pp1)
{
  int got = 0;

  if (pkg_mj == NULL || core_mj == NULL || dram_mj == NULL || has_pkg == NULL || has_core == NULL ||
      has_dram == NULL)
    return -1;
  *pkg_mj = *core_mj = *dram_mj = 0;
  *has_pkg = *has_core = *has_dram = 0;
  if (pp1_mj != NULL && has_pp1 != NULL) {
    *pp1_mj = 0;
    *has_pp1 = 0;
  }

#ifdef HAVE_LIKWID
  if (g_pwr_ready && g_pwr_group >= 0 && cpu_id >= 0) {
    int n_events;
    int i;

    if (perfmon_readGroupCounters(g_pwr_group) >= 0) {
      n_events = perfmon_getNumberOfEvents(g_pwr_group);
      for (i = 0; i < n_events; i++) {
        const char *event_name = perfmon_getEventName(g_pwr_group, i);
        const char *key;
        double raw;
        unsigned long long mj = 0;

        key = likwid_rapl_pwr_schema_key_from_event(event_name, g_pwr_amd_path);
        if (key == NULL)
          continue;
        raw = likwid_rapl_pwr_best_result(g_pwr_group, i, cpu_id);
        if (!likwid_rapl_pwr_result_usable(raw))
          continue;
        mj = likwid_rapl_joules_to_mj(raw);
        if (mj == 0)
          continue;
        if (strcmp(key, "pkg_energy") == 0) {
          *pkg_mj = mj;
          *has_pkg = 1;
          got = 1;
        } else if (strcmp(key, "pp0_energy") == 0 || strcmp(key, "core_energy") == 0) {
          *core_mj = mj;
          *has_core = 1;
          got = 1;
        } else if (strcmp(key, "pp1_energy") == 0) {
          if (pp1_mj != NULL && has_pp1 != NULL) {
            *pp1_mj = mj;
            *has_pp1 = 1;
            got = 1;
          }
        } else if (strcmp(key, "dram_energy") == 0) {
          *dram_mj = mj;
          *has_dram = 1;
          got = 1;
        }
      }
    }
    if (got)
      return 0;
    TRACE("likwid_rapl_pwr: no usable PWR energy for cpu_id=%d; trying powercap\n", cpu_id);
  }
#else
  (void)cpu_id;
#endif

  if (g_powercap_ok || rapl_powercap_available()) {
    if (rapl_powercap_collect_socket_mj(socket_id, pkg_mj, core_mj, dram_mj, has_pkg, has_core,
                                        has_dram, pp1_mj, has_pp1, g_pwr_amd_path) == 0)
      return 0;
  }

  TRACE("likwid_rapl_pwr: no energy results for cpu_id=%d socket=%u\n", cpu_id, socket_id);
  return -1;
}
