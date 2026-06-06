/*! \file likwid_uncore_adapter.c
 *  LIKWID uncore perfmon bridge for Intel IMC/CBO/CHA/QPI collectors.
 */

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "cpu_counter_metrics_likwid_begin.h"
#include "likwid_uncore_adapter.h"
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
    (void) dup2(*null_fd, STDERR_FILENO);
  return 1;
}

static void likwid_uncore_restore_stderr(int saved_stderr, int null_fd)
{
  if (saved_stderr >= 0) {
    (void) dup2(saved_stderr, STDERR_FILENO);
    close(saved_stderr);
  }
  if (null_fd >= 0)
    close(null_fd);
}

int likwid_uncore_adapter_begin(struct stats_type *type,
                                likwid_uncore_profile_t profile)
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

void likwid_uncore_adapter_collect(struct stats_type *type,
                                   likwid_uncore_profile_t profile)
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
    const char *counter_name =
        perfmon_getCounterName(g_profile_group[profile], i);
    char dev[32];
    const char *key = NULL;
    struct stats *stats = NULL;
    unsigned long long val;

    if (likwid_uncore_profile_map_counter(profile, counter_name, dev,
                                          sizeof(dev), &key) < 0)
      continue;
    val = (unsigned long long)perfmon_getResult(g_profile_group[profile], i,
                                                thread_id);
    stats = get_current_stats(type, dev);
    if (stats != NULL && key != NULL)
      stats_set(stats, key, val);
  }
#else
  (void)type;
  (void)profile;
#endif
}
