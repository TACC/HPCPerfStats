/*! \file likwid_uncore_adapter.c
 *  LIKWID uncore perfmon bridge for Intel IMC and AMD DF collectors.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cpu_counter_metrics_likwid_begin.h"
#include "host_edac_mem_topology.h"
#include "likwid_result_convert.h"
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
/* setupCounters programs the group; startCounters may already be running from host_cpu_hw. */
static int likwid_uncore_finish_group(int group)
{
  if (perfmon_setupCounters(group) < 0)
    return -1;
  /* startCounters may fail if host_cpu_hw already started the session; setup is enough. */
  (void)perfmon_startCounters();
  return 0;
}

/*
 * Try one eventset. On failure sets *fail_step to "addEventSet" or "setupCounters"
 * and *errno_out to errno captured at that step (0 if unused).
 * Returns 0 on success, -1 on failure.
 */
static int likwid_uncore_try_eventset(const char *events, int *group_out, const char **fail_step,
                                      int *errno_out)
{
  int group;

  if (fail_step != NULL)
    *fail_step = NULL;
  if (errno_out != NULL)
    *errno_out = 0;
  if (events == NULL || events[0] == '\0' || group_out == NULL)
    return -1;

  errno = 0;
  group = perfmon_addEventSet(events);
  if (group < 0) {
    if (errno_out != NULL)
      *errno_out = errno;
    if (fail_step != NULL)
      *fail_step = "addEventSet";
    return -1;
  }
  errno = 0;
  if (likwid_uncore_finish_group(group) < 0) {
    if (errno_out != NULL)
      *errno_out = errno;
    if (fail_step != NULL)
      *fail_step = "setupCounters";
    return -1;
  }
  *group_out = group;
  return 0;
}

static int likwid_uncore_spr_enable(likwid_uncore_profile_t profile, int group, const char *label,
                                    int index, const char *st_name)
{
  const char *name = st_name != NULL ? st_name : "intel_x86_uncore_imc";

  g_profile_group[profile] = group;
  g_profile_ready[profile] = 1;
  monitor_log_info("%s: enabled with eventset %s (index %d)\n", name, label, index);
  if (index > 0)
    monitor_log_warn("%s: using LIKWID fallback eventset %s (index %d)\n", name, label, index);
  return 0;
}

/*
 * After setup, reject eventsets whose MBOX counters are all non-finite (NaN →
 * 2^63 poison). HBM-only sets have no MBOX and always pass.
 * Returns 0 if acceptable, -1 if all MBOX results are unusable.
 */
static int likwid_uncore_spr_mbox_results_ok(int group)
{
  int n_events;
  int i;
  int n_mbox = 0;
  int n_ok = 0;

  if (perfmon_readGroupCounters(group) < 0)
    return -1;
  n_events = perfmon_getNumberOfEvents(group);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(group, i);
    unsigned long long unused = 0;
    double raw;

    if (counter_name == NULL || strncmp(counter_name, "MBOX", 4) != 0)
      continue;
    n_mbox++;
    raw = perfmon_getResult(group, i, 0);
    if (likwid_result_to_ull(raw, LIKWID_RESULT_U48_MAX, &unused) == 0)
      n_ok++;
  }
  if (n_mbox == 0)
    return 0;
  return n_ok > 0 ? 0 : -1;
}

static int likwid_uncore_adapter_begin_spr(struct stats_type *type, likwid_uncore_profile_t profile)
{
  likwid_spr_imc_eventset_t order[3];
  int hbm_sizes[3];
  int has_ddr = 0;
  int has_hbm = 0;
  int n_order;
  int n_hbm;
  int saved_stderr = -1;
  int null_fd = -1;
  int quiet = 0;
  int i;
  int total_tries;
  int try_idx = 0;
  const char *st_name =
      type != NULL && type->st_name != NULL ? type->st_name : "intel_x86_uncore_imc";

  if (g_profile_ready[profile])
    return 0;

  (void)host_edac_scan_mem_classes(&has_ddr, &has_hbm);
  n_order = likwid_spr_imc_eventset_try_order(has_ddr, has_hbm, order,
                                              (int)(sizeof(order) / sizeof(order[0])));
  n_hbm =
      likwid_spr_imc_hbm_ladder_sizes(hbm_sizes, (int)(sizeof(hbm_sizes) / sizeof(hbm_sizes[0])));
  total_tries = n_order + n_hbm;
  monitor_log_info("%s: EDAC has_ddr=%d has_hbm=%d; trying %d eventset(s), "
                   "primary %s (+ %d HBM ladder)\n",
                   st_name, has_ddr, has_hbm, total_tries,
                   n_order > 0 ? likwid_spr_imc_eventset_variant_name(order[0]) : "none", n_hbm);

  for (i = 0; i < n_order; i++) {
    const char *label = likwid_spr_imc_eventset_variant_name(order[i]);
    const char *events = likwid_spr_imc_eventset_string(order[i]);
    const char *fail_step = NULL;
    int fail_errno = 0;
    int group = -1;
    int last = (try_idx == total_tries - 1);

    if (!last && !quiet)
      quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);
    if (last && quiet) {
      likwid_uncore_restore_stderr(saved_stderr, null_fd);
      saved_stderr = -1;
      null_fd = -1;
      quiet = 0;
    }

    if (likwid_uncore_try_eventset(events, &group, &fail_step, &fail_errno) == 0) {
      if (likwid_uncore_spr_mbox_results_ok(group) == 0) {
        if (quiet) {
          likwid_uncore_restore_stderr(saved_stderr, null_fd);
          quiet = 0;
        }
        return likwid_uncore_spr_enable(profile, group, label, try_idx, st_name);
      }
      fail_step = "mboxResults";
      fail_errno = 0;
      monitor_log_warn("%s: eventset %s failed at %s "
                       "(all MBOX results non-finite)\n",
                       st_name, label, fail_step);
      if (quiet) {
        likwid_uncore_restore_stderr(saved_stderr, null_fd);
        saved_stderr = -1;
        null_fd = -1;
        quiet = 0;
      }
      try_idx++;
      continue;
    }
    if (quiet) {
      likwid_uncore_restore_stderr(saved_stderr, null_fd);
      saved_stderr = -1;
      null_fd = -1;
      quiet = 0;
    }
    monitor_log_warn("%s: eventset %s failed at %s (errno=%d)\n", st_name, label,
                     fail_step != NULL ? fail_step : "unknown", fail_errno);
    try_idx++;
  }

  for (i = 0; i < n_hbm; i++) {
    char label[32];
    const char *events = likwid_spr_imc_hbm_channels_eventset(hbm_sizes[i]);
    const char *fail_step = NULL;
    int fail_errno = 0;
    int group = -1;
    int last = (try_idx == total_tries - 1);

    snprintf(label, sizeof(label), "HBM%d", hbm_sizes[i]);
    if (!last && !quiet)
      quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);
    if (last && quiet) {
      likwid_uncore_restore_stderr(saved_stderr, null_fd);
      saved_stderr = -1;
      null_fd = -1;
      quiet = 0;
    }

    if (likwid_uncore_try_eventset(events, &group, &fail_step, &fail_errno) == 0) {
      /* HBM ladder has no MBOX; mbox gate is a no-op pass. */
      if (quiet) {
        likwid_uncore_restore_stderr(saved_stderr, null_fd);
        quiet = 0;
      }
      return likwid_uncore_spr_enable(profile, group, label, try_idx, st_name);
    }
    if (quiet) {
      likwid_uncore_restore_stderr(saved_stderr, null_fd);
      saved_stderr = -1;
      null_fd = -1;
      quiet = 0;
    }
    monitor_log_warn("%s: eventset %s failed at %s (errno=%d)\n", st_name, label,
                     fail_step != NULL ? fail_step : "unknown", fail_errno);
    try_idx++;
  }

  if (quiet)
    likwid_uncore_restore_stderr(saved_stderr, null_fd);
  monitor_log_error("%s: all LIKWID eventset variants failed "
                    "(has_ddr=%d has_hbm=%d); disabling type\n",
                    st_name, has_ddr, has_hbm);
  type->st_enabled = 0;
  return -1;
}

static int likwid_uncore_adapter_begin_icx(struct stats_type *type, likwid_uncore_profile_t profile)
{
  likwid_icx_imc_eventset_t order[3];
  int n_order;
  int saved_stderr = -1;
  int null_fd = -1;
  int quiet = 0;
  int i;
  const char *st_name =
      type != NULL && type->st_name != NULL ? type->st_name : "intel_x86_uncore_imc_icx";

  if (g_profile_ready[profile])
    return 0;

  n_order = likwid_icx_imc_eventset_try_order(order, (int)(sizeof(order) / sizeof(order[0])));
  monitor_log_info("%s: trying %d ICX IMC eventset(s), primary %s\n", st_name, n_order,
                   n_order > 0 ? likwid_icx_imc_eventset_variant_name(order[0]) : "none");

  for (i = 0; i < n_order; i++) {
    const char *label = likwid_icx_imc_eventset_variant_name(order[i]);
    const char *events = likwid_icx_imc_eventset_string(order[i]);
    const char *fail_step = NULL;
    int fail_errno = 0;
    int group = -1;
    int last = (i == n_order - 1);

    if (!last && !quiet)
      quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);
    if (last && quiet) {
      likwid_uncore_restore_stderr(saved_stderr, null_fd);
      saved_stderr = -1;
      null_fd = -1;
      quiet = 0;
    }

    if (likwid_uncore_try_eventset(events, &group, &fail_step, &fail_errno) == 0) {
      if (quiet) {
        likwid_uncore_restore_stderr(saved_stderr, null_fd);
        quiet = 0;
      }
      return likwid_uncore_spr_enable(profile, group, label, i, st_name);
    }
    if (quiet) {
      likwid_uncore_restore_stderr(saved_stderr, null_fd);
      saved_stderr = -1;
      null_fd = -1;
      quiet = 0;
    }
    monitor_log_warn("%s: eventset %s (`%s`) failed at %s (errno=%d)\n", st_name, label,
                     events != NULL ? events : "", fail_step != NULL ? fail_step : "unknown",
                     fail_errno);
  }

  if (quiet)
    likwid_uncore_restore_stderr(saved_stderr, null_fd);
  monitor_log_error("%s: all LIKWID ICX IMC eventset variants failed; disabling type\n", st_name);
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
  if (!likwid_uncore_profile_matches_processor(profile, processor)) {
    monitor_log_info("%s: disabled (processor does not match uncore profile)\n",
                     type->st_name != NULL ? type->st_name : "uncore");
    goto disable;
  }
  if (!cpu_counter_metrics_likwid_ready()) {
    monitor_log_error("%s: disabled (LIKWID PMC session not ready; host_cpu_hw must init first)\n",
                      type->st_name != NULL ? type->st_name : "uncore");
    goto disable;
  }

  if (profile == LIKWID_UNCORE_PROFILE_IMC_SPR || profile == LIKWID_UNCORE_PROFILE_IMC_EMR)
    return likwid_uncore_adapter_begin_spr(type, profile);
  if (profile == LIKWID_UNCORE_PROFILE_IMC_ICX)
    return likwid_uncore_adapter_begin_icx(type, profile);

  events = likwid_uncore_profile_eventset(profile);
  if (events == NULL || events[0] == '\0')
    goto disable;

  if (g_profile_ready[profile])
    return 0;

  quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);
  g_profile_group[profile] = perfmon_addEventSet(events);
  if (g_profile_group[profile] < 0)
    goto err;
  if (likwid_uncore_finish_group(g_profile_group[profile]) < 0)
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
  monitor_log_error("%s: LIKWID eventset setup failed (events=`%s`); disabling type\n",
                    type->st_name != NULL ? type->st_name : "uncore", events != NULL ? events : "");
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
  if (perfmon_readGroupCounters(g_profile_group[profile]) < 0)
    return;

  n_events = perfmon_getNumberOfEvents(g_profile_group[profile]);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(g_profile_group[profile], i);
    unsigned long long val = 0;
    double raw;

    raw = perfmon_getResult(g_profile_group[profile], i, thread_id);
    if (likwid_result_to_ull(raw, LIKWID_RESULT_U48_MAX, &val) < 0)
      continue;
    likwid_uncore_adapter_emit_counter(type, profile, counter_name, val);
  }
#else
  (void)type;
  (void)profile;
#endif
}
