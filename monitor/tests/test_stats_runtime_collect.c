/* stats_runtime_collect_cycle and stats_schema_key_active_this_phase. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "collect.h"
#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_runtime.h"
#include "stats_sink.h"

static struct stats_type g_type;
static int g_collect_calls;

void cpu_stats_invalidate_file_caches(void) {}
void net_stats_invalidate_iface_cache(void) {}
void auto_disable_optional_stats_by_lspci(void) {}
void metric_profiler_collect_begin(const char *name) { (void) name; }
void metric_profiler_collect_end(const char *name) { (void) name; }
void metric_profiler_cycle_begin(void) {}
void metric_profiler_cycle_end(FILE *stream) { (void) stream; }
void monitor_log_error(const char *fmt, ...) { (void) fmt; }
void monitor_log_warn(const char *fmt, ...) { (void) fmt; }
void collect_set_key_active_hook(collect_key_active_fn fn, void *ctx)
{
  (void) fn;
  (void) ctx;
}

int stats_type_init(struct stats_type *type)
{
  (void) type;
  return 0;
}

void stats_type_destroy(struct stats_type *type) { (void) type; }

struct stats_type *stats_type_for_each(size_t *i)
{
  if (*i != 0)
    return NULL;
  (*i)++;
  return &g_type;
}

struct stats_type *stats_type_get(const char *name)
{
  (void) name;
  return NULL;
}

static void stub_collect(struct stats_type *type)
{
  (void) type;
  g_collect_calls++;
}

static int finalize_ok(void *opaque)
{
  int *n = opaque;
  if (n != NULL)
    (*n)++;
  return 0;
}

static int finalize_fail(void *opaque)
{
  (void) opaque;
  return -1;
}

static void setup_type(const char *schema_def)
{
  memset(&g_type, 0, sizeof(g_type));
  g_collect_calls = 0;
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "host_tt");
  assert(schema_init(&g_type.st_schema, schema_def) == 0);
  collect_tier_apply_to_type(&g_type);
  g_type.st_enabled = 1;
  g_type.st_collect = stub_collect;
}

static void teardown_type(void)
{
  schema_destroy(&g_type.st_schema);
}

static void test_collect_cycle_runs_collect_and_finalize(void)
{
  struct stats_sink_ops sink = { .finalize = finalize_ok };
  int finalize_calls = 0;

  setup_type("a,E b,E,R=S");
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(stats_runtime_collect_cycle(NULL, &finalize_calls, &sink, 0) == 0);
  assert(g_collect_calls == 1);
  assert(finalize_calls == 1);
  teardown_type();
}

static void test_collect_cycle_propagates_finalize_error(void)
{
  struct stats_sink_ops sink = { .finalize = finalize_fail };

  setup_type("a,E");
  assert(stats_runtime_collect_cycle(NULL, NULL, &sink, 0) == -1);
  assert(g_collect_calls == 1);
  teardown_type();
}

static void test_schema_key_active_this_phase(void)
{
  setup_type("fast_k,E slow_k,E,R=S");
  collect_tier_set_enabled(1);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(stats_schema_key_active_this_phase(&g_type, 0) == 1);
  assert(stats_schema_key_active_this_phase(&g_type, 1) == 0);

  collect_tier_set_phase(COLLECT_FULL);
  assert(stats_schema_key_active_this_phase(&g_type, 1) == 1);

  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FAST_ONLY);
  assert(stats_schema_key_active_this_phase(&g_type, 1) == 1);
  teardown_type();
}

int main(void)
{
  test_collect_cycle_runs_collect_and_finalize();
  test_collect_cycle_propagates_finalize_error();
  test_schema_key_active_this_phase();
  printf("test_stats_runtime_collect passed\n");
  return 0;
}
