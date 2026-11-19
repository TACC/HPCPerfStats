/* Every type begins in plain registry order, host_cpu_hw included: each CPU backend (x86 and arm)
 * and every uncore / RAPL collector opens its own perf fds, so no type depends on another having
 * begun first. Covers the daemon and the batch (main) prepare paths. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collect.h"
#include "cpu_counter_metrics.h"
#include "stats.h"
#include "stats_runtime.h"

#define N_TYPES 4

static struct stats_type g_types[N_TYPES];
static int g_begin_calls[N_TYPES];
static int g_begin_order[N_TYPES];
static int g_begin_order_len;

static int find_type_index(struct stats_type *type)
{
  size_t i;

  for (i = 0; i < N_TYPES; i++) {
    if (type == &g_types[i])
      return (int)i;
  }
  return -1;
}

static int record_begin(struct stats_type *type)
{
  int idx = find_type_index(type);

  assert(idx >= 0);
  g_begin_calls[idx]++;
  assert(g_begin_order_len < N_TYPES);
  g_begin_order[g_begin_order_len++] = idx;
  return 0;
}

void cpu_stats_invalidate_file_caches(void) {}
void net_stats_invalidate_iface_cache(void) {}
void auto_disable_optional_stats_by_lspci(void) {}
void metric_profiler_collect_begin(const char *name)
{
  (void)name;
}
void metric_profiler_collect_end(const char *name)
{
  (void)name;
}
void metric_profiler_cycle_begin(void) {}
void metric_profiler_cycle_end(FILE *stream)
{
  (void)stream;
}
void monitor_log_error(const char *fmt, ...)
{
  (void)fmt;
}
void monitor_log_warn(const char *fmt, ...)
{
  (void)fmt;
}
void collect_set_key_active_hook(collect_key_active_fn fn, void *ctx)
{
  (void)fn;
  (void)ctx;
}

int stats_type_init(struct stats_type *type)
{
  (void)type;
  return 0;
}

void stats_type_mark_not_updated(struct stats_type *type)
{
  (void)type;
}

void stats_type_prune_untouched(struct stats_type *type)
{
  (void)type;
}

void stats_type_destroy(struct stats_type *type)
{
  (void)type;
}

struct stats_type *stats_type_for_each(size_t *i)
{
  if (*i >= N_TYPES)
    return NULL;
  return &g_types[(*i)++];
}

struct stats_type *stats_type_get(const char *name)
{
  size_t i;

  for (i = 0; i < N_TYPES; i++) {
    if (strcmp(g_types[i].st_name, name) == 0)
      return &g_types[i];
  }
  return NULL;
}

static void reset_state(void)
{
  memset(g_types, 0, sizeof(g_types));
  memset(g_begin_calls, 0, sizeof(g_begin_calls));
  memset(g_begin_order, 0, sizeof(g_begin_order));
  g_begin_order_len = 0;

  snprintf(g_types[0].st_name, sizeof(g_types[0].st_name), "%s", "amd_x86_uncore_df_turin");
  g_types[0].st_begin = record_begin;
  g_types[0].st_enabled = 1;

  snprintf(g_types[1].st_name, sizeof(g_types[1].st_name), "%s", "amd_x86_rapl");
  g_types[1].st_begin = record_begin;
  g_types[1].st_enabled = 1;

  snprintf(g_types[2].st_name, sizeof(g_types[2].st_name), "%s", CPU_COUNTER_METRICS_ST_NAME);
  g_types[2].st_begin = record_begin;
  g_types[2].st_enabled = 1;

  snprintf(g_types[3].st_name, sizeof(g_types[3].st_name), "%s", "host_net");
  g_types[3].st_begin = record_begin;
  g_types[3].st_enabled = 1;

  unsetenv("HPCPERFSTATS_DISABLE_TYPES");
  stats_runtime_daemon_set_type_controls("default", NULL);
  stats_runtime_daemon_reset_types();
}

static void assert_registry_order(void)
{
  int i;

  assert(g_begin_order_len == N_TYPES);
  for (i = 0; i < N_TYPES; i++) {
    assert(g_begin_order[i] == i);
    assert(g_begin_calls[i] == 1);
  }
}

static void test_daemon_prepare_begins_in_registry_order(void)
{
  reset_state();
  stats_runtime_daemon_prepare_types();
  assert_registry_order();
  stats_runtime_daemon_reset_types();
}

static void test_main_prepare_begins_in_registry_order(void)
{
  stats_runtime_main_prepare_spec spec = {
      .enable_all = 0,
      .select_all = 0,
      .call_begin = 1,
  };

  reset_state();
  stats_runtime_main_prepare_types(&spec);
  assert_registry_order();
  stats_runtime_daemon_reset_types();
}

int main(void)
{
  test_daemon_prepare_begins_in_registry_order();
  test_main_prepare_begins_in_registry_order();
  printf("test_stats_runtime_begin_order passed\n");
  return 0;
}
