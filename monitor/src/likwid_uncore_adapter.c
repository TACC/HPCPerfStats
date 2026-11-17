/*! \file likwid_uncore_adapter.c
 *  LIKWID uncore perfmon bridge for Intel IMC and AMD DF collectors.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
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

#define LIKWID_UNCORE_PLAN_DEVS 32
#define LIKWID_UNCORE_COUNTER_NAME_MAX 32

static int g_profile_group[LIKWID_UNCORE_PROFILE_COUNT];
static int g_profile_ready[LIKWID_UNCORE_PROFILE_COUNT];
/* Eventset of the ready group; static string literals from likwid_uncore_profiles.c. */
static const char *g_profile_events[LIKWID_UNCORE_PROFILE_COUNT];

/* Devices an eventset programs, each with a bit per schema index it programs. */
struct likwid_uncore_plan {
  struct {
    char dev[16];
    unsigned long long keys;
  } devs[LIKWID_UNCORE_PLAN_DEVS];
  int n;
  int planned;
};
static struct likwid_uncore_plan g_profile_plan[LIKWID_UNCORE_PROFILE_COUNT];

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
static void likwid_uncore_unquiet(int *quiet, int *saved_stderr, int *null_fd)
{
  if (!*quiet)
    return;
  likwid_uncore_restore_stderr(*saved_stderr, *null_fd);
  *saved_stderr = -1;
  *null_fd = -1;
  *quiet = 0;
}

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

static int likwid_uncore_spr_enable(likwid_uncore_profile_t profile, int group, const char *events,
                                    const char *label, int index, const char *st_name)
{
  const char *name = st_name != NULL ? st_name : "intel_x86_uncore_imc";

  g_profile_group[profile] = group;
  g_profile_events[profile] = events;
  g_profile_ready[profile] = 1;
  monitor_log_info("%s: enabled with eventset %s (index %d)\n", name, label, index);
  if (index > 0)
    monitor_log_warn("%s: using LIKWID fallback eventset %s (index %d)\n", name, label, index);
  return 0;
}

/*
 * After setup, reject eventsets whose MBOX counters are all non-finite (NaN ->
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

    if (!last)
      quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);

    if (likwid_uncore_try_eventset(events, &group, &fail_step, &fail_errno) == 0) {
      if (likwid_uncore_spr_mbox_results_ok(group) == 0) {
        likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
        return likwid_uncore_spr_enable(profile, group, events, label, try_idx, st_name);
      }
      fail_step = "mboxResults";
      fail_errno = 0;
      monitor_log_warn("%s: eventset %s failed at %s "
                       "(all MBOX results non-finite)\n",
                       st_name, label, fail_step);
      likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
      try_idx++;
      continue;
    }
    likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
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
    if (!last)
      quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);

    if (likwid_uncore_try_eventset(events, &group, &fail_step, &fail_errno) == 0) {
      /* HBM ladder has no MBOX; mbox gate is a no-op pass. */
      likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
      return likwid_uncore_spr_enable(profile, group, events, label, try_idx, st_name);
    }
    likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
    monitor_log_warn("%s: eventset %s failed at %s (errno=%d)\n", st_name, label,
                     fail_step != NULL ? fail_step : "unknown", fail_errno);
    try_idx++;
  }

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

    if (!last)
      quiet = likwid_uncore_quiet_stderr(&saved_stderr, &null_fd);

    if (likwid_uncore_try_eventset(events, &group, &fail_step, &fail_errno) == 0) {
      likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
      return likwid_uncore_spr_enable(profile, group, events, label, i, st_name);
    }
    likwid_uncore_unquiet(&quiet, &saved_stderr, &null_fd);
    monitor_log_warn("%s: eventset %s (`%s`) failed at %s (errno=%d)\n", st_name, label,
                     events != NULL ? events : "", fail_step != NULL ? fail_step : "unknown",
                     fail_errno);
  }

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
  g_profile_events[profile] = events;
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

/* Row and key a LIKWID counter name maps to, or NULL. */
static struct stats *likwid_uncore_counter_row(struct stats_type *type,
                                               likwid_uncore_profile_t profile,
                                               const char *counter_name, const char **key)
{
  char dev[32];

  if (type == NULL || counter_name == NULL)
    return NULL;
  if (likwid_uncore_profile_map_counter(profile, counter_name, dev, sizeof(dev), key) < 0 ||
      *key == NULL)
    return NULL;
  return get_current_stats(type, dev);
}

void likwid_uncore_adapter_emit_counter(struct stats_type *type, likwid_uncore_profile_t profile,
                                        const char *counter_name, unsigned long long val)
{
  const char *key = NULL;
  struct stats *stats = likwid_uncore_counter_row(type, profile, counter_name, &key);

  if (stats != NULL)
    stats_set(stats, key, val);
}

/* Adds the counter `name` to `plan`; -1 when it maps to no schema key or the table is full. */
static int likwid_uncore_plan_add(struct likwid_uncore_plan *plan, struct stats_type *type,
                                  likwid_uncore_profile_t profile, const char *name)
{
  char dev[16];
  const char *key = NULL;
  int k;
  int d;

  if (likwid_uncore_profile_map_counter(profile, name, dev, sizeof(dev), &key) < 0)
    return -1;
  k = schema_ref(&type->st_schema, key);
  if (k < 0 || k >= 64)
    return -1;
  for (d = 0; d < plan->n && strcmp(plan->devs[d].dev, dev) != 0; d++)
    ;
  if (d == plan->n) {
    if (d == LIKWID_UNCORE_PLAN_DEVS)
      return -1;
    snprintf(plan->devs[d].dev, sizeof(plan->devs[d].dev), "%s", dev);
    plan->devs[d].keys = 0;
    plan->n++;
  }
  plan->devs[d].keys |= 1ULL << k;
  return 0;
}

int likwid_uncore_adapter_plan(struct stats_type *type, likwid_uncore_profile_t profile,
                               const char *events)
{
  struct likwid_uncore_plan *plan;
  const char *p = events;

  if (type == NULL || events == NULL || profile < 0 || profile >= LIKWID_UNCORE_PROFILE_COUNT)
    return -1;
  plan = &g_profile_plan[profile];
  plan->planned = 1;
  plan->n = 0;
  /* EVENT:COUNTER[,EVENT:COUNTER...] */
  while ((p = strchr(p, ':')) != NULL) {
    char name[LIKWID_UNCORE_COUNTER_NAME_MAX];
    size_t len = strcspn(++p, ",");

    if (len >= sizeof(name))
      len = sizeof(name) - 1;
    memcpy(name, p, len);
    name[len] = '\0';
    if (likwid_uncore_plan_add(plan, type, profile, name) < 0) {
      plan->n = 0;
      return -1;
    }
    p += len;
  }
  return plan->n > 0 ? 0 : -1;
}

void likwid_uncore_adapter_mark_unprogrammed(struct stats_type *type,
                                             likwid_uncore_profile_t profile)
{
  const struct likwid_uncore_plan *plan;

  if (type == NULL || profile < 0 || profile >= LIKWID_UNCORE_PROFILE_COUNT)
    return;
  plan = &g_profile_plan[profile];
  for (int d = 0; d < plan->n; d++) {
    struct stats *stats = get_current_stats(type, plan->devs[d].dev);

    for (size_t k = 0; stats != NULL && k < type->st_schema.sc_len; k++)
      if (k >= 64 || !(plan->devs[d].keys & (1ULL << k)))
        stats_set_missing(stats, type->st_schema.sc_ent[k]->se_key, STATS_SENTINEL_NOT_BUILT);
  }
}

#ifdef HAVE_LIKWID
/* A failed group read: every planned device's keys not marked yet this cycle get `code`. */
static void likwid_uncore_mark_planned(struct stats_type *type, likwid_uncore_profile_t profile,
                                       enum stats_sentinel code)
{
  const struct likwid_uncore_plan *plan = &g_profile_plan[profile];

  for (int d = 0; d < plan->n; d++)
    stats_set_missing_all(get_current_stats(type, plan->devs[d].dev), code);
}
#endif

void likwid_uncore_adapter_collect(struct stats_type *type, likwid_uncore_profile_t profile)
{
#ifdef HAVE_LIKWID
  int i;
  int n_events = 0;
  int thread_id = 0;
  int group;
  static time_t last_warn;

  if (type == NULL || profile < 0 || profile >= LIKWID_UNCORE_PROFILE_COUNT)
    return;
  if (!g_profile_ready[profile] || g_profile_group[profile] < 0)
    return;

  if (!g_profile_plan[profile].planned &&
      likwid_uncore_adapter_plan(type, profile, g_profile_events[profile]) < 0)
    monitor_log_warn("%s: eventset `%s` does not map onto the schema; unprogrammed keys emit -1\n",
                     type->st_name,
                     g_profile_events[profile] != NULL ? g_profile_events[profile] : "");
  likwid_uncore_adapter_mark_unprogrammed(type, profile);

  group = g_profile_group[profile];
  /*
   * host_cpu_hw prepare_collect re-arms the core group each tick; SKX/ICX
   * uncore_imc PERF stops unless we finish this group before read.
   */
  if (likwid_uncore_finish_group(group) < 0) {
    time_t now = time(NULL);

    if (last_warn == 0 || (now > last_warn && now - last_warn >= 60)) {
      monitor_log_warn("%s: perfmon_setupCounters(uncore group=%d) failed before "
                       "readGroupCounters; uncore keys emit BACKEND_ERROR\n",
                       type->st_name, group);
      last_warn = now;
    }
    likwid_uncore_mark_planned(type, profile, STATS_SENTINEL_BACKEND_ERROR);
    return;
  }
  if (perfmon_readGroupCounters(group) < 0) {
    likwid_uncore_mark_planned(type, profile, STATS_SENTINEL_BACKEND_ERROR);
    return;
  }

  n_events = perfmon_getNumberOfEvents(group);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(group, i);
    unsigned long long val = 0;
    double raw;

    raw = perfmon_getResult(group, i, thread_id);
    if (likwid_result_to_ull(raw, LIKWID_RESULT_U48_MAX, &val) < 0) {
      const char *key = NULL;
      struct stats *stats = likwid_uncore_counter_row(type, profile, counter_name, &key);

      if (stats != NULL)
        stats_set_missing(stats, key, STATS_SENTINEL_INVALID_VALUE);
      continue;
    }
    likwid_uncore_adapter_emit_counter(type, profile, counter_name, val);
  }
#else
  (void)type;
  (void)profile;
#endif
}
