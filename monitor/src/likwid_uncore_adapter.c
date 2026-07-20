/*! \file likwid_uncore_adapter.c
 *  LIKWID uncore perfmon bridge for Intel IMC/CBO/CHA/QPI collectors.
 */

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "cpu_counter_metrics_likwid_begin.h"
#include "host_edac_mem_topology.h"
#include "likwid_uncore_adapter.h"
#include "monitor_log.h"
#include "trace.h"

#ifdef HAVE_LIKWID
#include <likwid.h>
#endif

static int g_profile_group[LIKWID_UNCORE_PROFILE_COUNT];
static int g_profile_ready[LIKWID_UNCORE_PROFILE_COUNT];

static int likwid_uncore_quiet_stderr(int *saved_stderr, int *null_fd)
{
  *saved_stderr = -1;
  *null_fd = -1;
  *saved_stderr = dup(STDERR_FILENO);
  if (*saved_stderr < 0)
    return 0;
  *null_fd = open("/dev/null", O_WRONLY);
  if (*null_fd >= 0)
    (void)dup2(*null_fd, STDERR_FILENO);
  return 1;
}

static void likwid_uncore_restore_stderr(int saved_stderr, int null_fd)
{
  if (saved_stderr >= 0) {
    (void)dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (null_fd >= 0)
    close(null_fd);
}

#ifdef HAVE_LIKWID
static int likwid_uncore_try_eventset(const char *events, int *group_out, int saved_stderr,
                                      int null_fd, int quiet)
{
  int group;

  if (events == NULL || events[0] == '\0' || group_out == NULL)
    return -1;

  group = perfmon_addEventSet(events);
  if (group < 0)
    return -1;
  if (perfmon_setupCounters(group) < 0)
    return -1;
  if (perfmon_startCounters() < 0)
    return -1;
  *group_out = group;
  if (quiet)
    likwid_uncore_restore_stderr(saved_stderr, null_fd);
  return 0;
}

static int likwid_uncore_adapter_begin_spr(struct stats_type *type)
{
  likwid_spr_imc_eventset_t order[3];
  int has_ddr = 0;
  int has_hbm = 0;
  int n_order;
  int saved_stderr = -1;
  int null_fd = -1;
  int quiet = 0;
  int i;

  if (g_profile_ready[LIKWID_UNCORE_PROFILE_IMC_SPR])
    return 0;

  (void)host_edac_scan_mem_classes(&has_ddr, &has_hbm);
  n_order = likwid_spr_imc_eventset_try_order(has_ddr, has_hbm, order,
                                              (int)(sizeof(order) / sizeof(order[0])));
  quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);
  for (i = 0; i < n_order; i++) {
    const char *events = likwid_spr_imc_eventset_string(order[i]);
    int group = -1;

    if (likwid_uncore_try_eventset(events, &group, saved_stderr, null_fd, quiet) == 0) {
      g_profile_group[LIKWID_UNCORE_PROFILE_IMC_SPR] = group;
      g_profile_ready[LIKWID_UNCORE_PROFILE_IMC_SPR] = 1;
      if (i > 0) {
        monitor_log_warn("intel_x86_uncore_imc_spr: using LIKWID fallback eventset index %d\n", i);
      }
      return 0;
    }
  }
  if (quiet)
    likwid_uncore_restore_stderr(saved_stderr, null_fd);
  type->st_enabled = 0;
  return -1;
}
#endif

int likwid_uncore_adapter_begin(struct stats_type *type, likwid_uncore_profile_t profile)
{
#ifdef HAVE_LIKWID
  const char *events = NULL;
  int saved_stderr = -1;
  int null_fd = -1;
  int quiet = 0;

  if (type == NULL || profile < 0 || profile >= LIKWID_UNCORE_PROFILE_COUNT)
    return -1;
  if (!likwid_uncore_profile_matches_processor(profile, processor))
    goto disable;
  if (!cpu_counter_metrics_likwid_ready())
    goto disable;

  if (profile == LIKWID_UNCORE_PROFILE_IMC_SPR)
    return likwid_uncore_adapter_begin_spr(type);

  events = likwid_uncore_profile_eventset(profile);
  if (events == NULL || events[0] == '\0')
    goto disable;

  if (g_profile_ready[profile])
    return 0;

  quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);
  g_profile_group[profile] = perfmon_addEventSet(events);
  if (g_profile_group[profile] < 0)
    goto err;
  if (perfmon_setupCounters(g_profile_group[profile]) < 0)
    goto err;
  if (perfmon_startCounters() < 0)
    goto err;
  if (quiet)
    likwid_uncore_restore_stderr(saved_stderr, null_fd);
  g_profile_ready[profile] = 1;
  return 0;

disable:
  type->st_enabled = 0;
  return -1;

err:
  if (quiet)
    likwid_uncore_restore_stderr(saved_stderr, null_fd);
  type->st_enabled = 0;
  return -1;
#else
  (void)type;
  (void)profile;
  return -1;
#endif
}

void likwid_uncore_adapter_emit_counter(struct stats_type *type, likwid_uncore_profile_t profile,
                                        const char *counter_name, unsigned long long val)
{
  char dev[32];
  const char *key = NULL;
  struct stats *stats = NULL;

  if (type == NULL || counter_name == NULL)
    return;
  if (likwid_uncore_profile_map_counter(profile, counter_name, dev, sizeof(dev), &key) < 0)
    return;
  stats = get_current_stats(type, dev);
  if (stats != NULL && key != NULL)
    stats_set(stats, key, val);
}

void likwid_uncore_adapter_collect(struct stats_type *type, likwid_uncore_profile_t profile)
{
#ifdef HAVE_LIKWID
  int i;
  int n_events = 0;
  int thread_id = 0;

  if (type == NULL || profile < 0 || profile >= LIKWID_UNCORE_PROFILE_COUNT)
    return;
  if (!g_profile_ready[profile] || g_profile_group[profile] < 0)
    return;
  if (perfmon_readCounters() < 0)
    return;

  n_events = perfmon_getNumberOfEvents(g_profile_group[profile]);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(g_profile_group[profile], i);
    unsigned long long val;

    val = (unsigned long long)perfmon_getResult(g_profile_group[profile], i, thread_id);
    likwid_uncore_adapter_emit_counter(type, profile, counter_name, val);
  }
#else
  (void)type;
  (void)profile;
#endif
}
