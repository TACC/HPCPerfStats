/* Daemon and batch runtime: enable types, profiles, collect cycles, teardown. */
#include "stats_runtime.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "collect.h"
#include "collect_tier.h"
#include "cpu_counter_metrics.h"
#include "hwdetect.h"
#include "metric_profiler.h"
#include "monitor_log.h"
#include "monitor_timing.h"
#include "stats.h"
#include "string1.h"
#include "trace.h"

static int g_daemon_types_ready;
static char *g_type_profile;
static char *g_disabled_types_csv;

/* collect.c read-gating hook: skip reads for keys inactive in the current phase. */
static int stats_runtime_collect_key_active(void *ctx, struct stats *stats, const char *key)
{
  int idx;

  (void)ctx;
  if (stats == NULL || key == NULL)
    return 1;
  idx = schema_ref(&stats->s_type->st_schema, key);
  if (idx < 0)
    return 1; /* unknown key: let stats_set drop it as before */
  return collect_tier_key_active(stats->s_type, idx);
}

static void stats_runtime_install_collect_tier_hook(void)
{
  collect_set_key_active_hook(stats_runtime_collect_key_active, NULL);
}

static void stats_runtime_disable_one_type(const char *name)
{
  struct stats_type *type;

  if (name == NULL || *name == '\0')
    return;

  type = stats_type_get(name);
  if (type == NULL) {
    monitor_log_warn("stats_runtime: unknown type `%s` in disable list\n", name);
    return;
  }
  type->st_enabled = 0;
}

static void stats_runtime_disable_types_from_csv(const char *csv)
{
  char *list;
  char *token;
  char *saveptr = NULL;

  if (csv == NULL || csv[0] == '\0')
    return;

  list = strdup(csv);
  if (list == NULL)
    return;

  for (token = strtok_r(list, ",", &saveptr); token != NULL;
       token = strtok_r(NULL, ",", &saveptr)) {
    str_trim_inplace(token);
    stats_runtime_disable_one_type(token);
  }
  free(list);
}

static void stats_runtime_apply_profile_and_disables(void)
{
  const char *env_csv = getenv("HPCPERFSTATS_DISABLE_TYPES");

  if (g_type_profile != NULL && strcmp(g_type_profile, "minimal") == 0)
    stats_runtime_disable_one_type("host_proc");

  stats_runtime_disable_types_from_csv(g_disabled_types_csv);
  stats_runtime_disable_types_from_csv(env_csv);
}

static long long stats_runtime_monotonic_us(void)
{
  struct timespec ts;

  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return -1;
  return (long long)ts.tv_sec * 1000000LL + (long long)ts.tv_nsec / 1000LL;
}

static void stats_runtime_init_one_type(struct stats_type *type)
{
  if (stats_type_init(type) < 0) {
    monitor_log_error("stats_runtime: disabling `%s` due to init failure\n", type->st_name);
    type->st_enabled = 0;
    return;
  }
  collect_tier_apply_to_type(type);
}

static void stats_runtime_begin_one_type(struct stats_type *type)
{
  if (type->st_begin != NULL)
    (*type->st_begin)(type);
}

/*! Registry iteration is sorted by st_name; AMD DF/RAPL types sort before host_cpu_hw.
 *  LIKWID HPMinit runs in host_cpu_hw begin, so begin that type first. */
static void stats_runtime_begin_enabled_types(void)
{
  size_t i = 0;
  struct stats_type *type;
#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_LIKWID)
  struct stats_type *host_cpu_hw;

  host_cpu_hw = stats_type_get(CPU_COUNTER_METRICS_ST_NAME);
  if (host_cpu_hw != NULL && host_cpu_hw->st_enabled)
    stats_runtime_begin_one_type(host_cpu_hw);
#endif

  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;
#if defined(MONITOR_WITH_HARDWARE) && defined(MONITOR_CPU_BACKEND_LIKWID)
    if (host_cpu_hw != NULL && type == host_cpu_hw)
      continue;
#endif
    stats_runtime_begin_one_type(type);
  }
}

void stats_runtime_teardown(void)
{
  size_t i = 0;
  struct stats_type *type;

  cpu_stats_invalidate_file_caches();
  net_stats_invalidate_iface_cache();
  while ((type = stats_type_for_each(&i)) != NULL)
    stats_type_destroy(type);
  g_daemon_types_ready = 0;
}

void stats_runtime_daemon_prepare_types(void)
{
  size_t i = 0;
  struct stats_type *type;

  while ((type = stats_type_for_each(&i)) != NULL)
    type->st_enabled = 1;

  auto_disable_optional_stats_by_lspci();
  stats_runtime_apply_profile_and_disables();
  stats_runtime_install_collect_tier_hook();

  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;
    stats_runtime_init_one_type(type);
  }
  stats_runtime_begin_enabled_types();
  g_daemon_types_ready = 1;
}

void stats_runtime_daemon_set_type_controls(const char *profile, const char *disable_csv)
{
  char *profile_copy = NULL;
  char *disable_copy = NULL;

  if (profile != NULL)
    profile_copy = strdup(profile);
  if (disable_csv != NULL)
    disable_copy = strdup(disable_csv);

  free(g_type_profile);
  free(g_disabled_types_csv);
  g_type_profile = profile_copy;
  g_disabled_types_csv = disable_copy;
}

void stats_runtime_daemon_reset_types(void)
{
  if (!g_daemon_types_ready)
    return;
  stats_runtime_teardown();
}

int stats_runtime_daemon_ensure_types(void)
{
  long long started_us;
  long long elapsed_us;

  if (g_daemon_types_ready)
    return 0;

  started_us = stats_runtime_monotonic_us();
  stats_runtime_daemon_prepare_types();
  if (started_us > 0) {
    elapsed_us = stats_runtime_monotonic_us() - started_us;
    if (elapsed_us > 50000LL)
      TRACE("stats_runtime daemon prepare slow: elapsed_us=%lld\n", elapsed_us);
  }
  return 0;
}

void stats_runtime_collect_enabled_metrics(int require_selected)
{
  size_t i = 0;
  struct stats_type *type;

  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;
    if (require_selected && !type->st_selected)
      continue;
    metric_profiler_collect_begin(type->st_name);
    (*type->st_collect)(type);
    metric_profiler_collect_end(type->st_name);
  }
}

void stats_runtime_main_prepare_types(const stats_runtime_main_prepare_spec *spec)
{
  size_t i = 0;
  struct stats_type *type;

  if (spec == NULL)
    return;

  stats_runtime_install_collect_tier_hook();
  auto_disable_optional_stats_by_lspci();

  if (spec->enable_all) {
    i = 0;
    while ((type = stats_type_for_each(&i)) != NULL)
      type->st_enabled = 1;
  }

  i = 0;
  while ((type = stats_type_for_each(&i)) != NULL) {
    if (!type->st_enabled)
      continue;
    if (stats_type_init(type) < 0) {
      monitor_log_error("stats_runtime: disabling `%s` due to init failure\n", type->st_name);
      type->st_enabled = 0;
      continue;
    }
    collect_tier_apply_to_type(type);
    if (spec->select_all)
      type->st_selected = 1;
  }
  if (spec->call_begin)
    stats_runtime_begin_enabled_types();
}

int stats_runtime_collect_cycle(FILE *profiler_stream, void *opaque,
                                const struct stats_sink_ops *sink, int require_selected)
{
  int rc = 0;
  FILE *prof_out = profiler_stream != NULL ? profiler_stream : stderr;

  metric_profiler_cycle_begin();

  stats_runtime_collect_enabled_metrics(require_selected);

  if (sink != NULL && sink->finalize != NULL)
    rc = sink->finalize(opaque);

  metric_profiler_cycle_end(prof_out);
  return rc;
}

void stats_runtime_set_collect_phase(enum collect_phase phase)
{
  collect_tier_set_phase(phase);
}

enum collect_phase stats_runtime_effective_collect_phase(int write_hdr)
{
  return collect_tier_effective_phase(write_hdr);
}

int stats_schema_key_active_this_phase(const struct stats_type *type, int idx)
{
  return collect_tier_key_active(type, idx);
}

enum collect_phase stats_runtime_collect_phase_for_tick(double now_sec, long long *last_slow_slot,
                                                        double sample_freq_slow)
{
  enum collect_phase phase = COLLECT_FAST_ONLY;
  long long prev = (last_slow_slot != NULL) ? *last_slow_slot : -1;

  if (!collect_tier_enabled()) {
    collect_tier_set_phase(COLLECT_FULL);
    return COLLECT_FULL;
  }

  if (monitor_collect_should_run_slow(now_sec, prev, sample_freq_slow)) {
    phase = COLLECT_FULL;
    if (last_slow_slot != NULL)
      *last_slow_slot = monitor_collect_slow_slot(now_sec, sample_freq_slow);
  }
  collect_tier_set_phase(phase);
  return phase;
}

static int stats_runtime_format_type_names_by_enabled(char *buf, size_t cap, int want_enabled)
{
  size_t i = 0;
  size_t used = 0;
  int first = 1;
  struct stats_type *type;

  if (buf == NULL || cap == 0)
    return -1;
  buf[0] = '\0';

  while ((type = stats_type_for_each(&i)) != NULL) {
    size_t name_len;
    size_t need;
    int is_on;

    if (type->st_name[0] == '\0')
      continue;
    is_on = type->st_enabled ? 1 : 0;
    if (want_enabled) {
      if (!is_on)
        continue;
    } else if (is_on) {
      continue;
    }
    name_len = strlen(type->st_name);
    need = name_len + (first ? 0u : 1u);
    if (used + need + 1u > cap)
      return -1;
    if (!first)
      buf[used++] = ',';
    memcpy(buf + used, type->st_name, name_len);
    used += name_len;
    buf[used] = '\0';
    first = 0;
  }
  return (int)used;
}

int stats_runtime_format_enabled_type_names(char *buf, size_t cap)
{
  return stats_runtime_format_type_names_by_enabled(buf, cap, 1);
}

int stats_runtime_format_disabled_type_names(char *buf, size_t cap)
{
  return stats_runtime_format_type_names_by_enabled(buf, cap, 0);
}
