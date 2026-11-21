/* Fault coverage for x86_uncore_perf.c (built with -DDEBUG in every build, with
 * test_site_coverage.c): publish on real stats.c rows. A key no event of the row's class feeds is
 * NOT_BUILT when the profile marks it so, else NOT_PRESENT; a fed key whose read failed this cycle
 * carries its code (never a partial or stale sum); the time keys follow the row's time status. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "collect_tier.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "x86_uncore_perf.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;

static const char *const k_keys[] = {"cas_reads", "cas_writes", "act_count"};
static const struct x86_uncore_event k_events[] = {{.key = 0, .sysfs_name = "cas_count_read"}};
static const struct x86_uncore_class k_class = {
    .pmu = "uncore_imc_", .indexed = 1, .dev = "mbox", .events = k_events, .n_events = 1};
static const struct x86_uncore_profile k_profile = {
    .keys = k_keys, .n_keys = 3, .not_built = 1U << 2, .classes = &k_class, .n_classes = 1};

static struct stats_type g_type;

static unsigned int state_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static void publish(struct x86_uncore_perf *u)
{
  stats_type_mark_not_updated(&g_type);
  x86_uncore_perf_publish(u, &g_type);
}

static void test_publish_codes(void)
{
  static struct x86_uncore_perf u;
  struct x86_uncore_dev *d = &u.dev[0];
  struct stats *s;

  u.profile = &k_profile;
  u.n = 1;
  snprintf(d->name, sizeof(d->name), "%s", "mbox3");
  d->cls = &k_class;
  d->ev = k_events; /* the row's own events: the whole class (not rows-split) */
  d->n_ev = 1;
  d->life[0] = (1ULL << 48) + 7; /* W=48 key: published modulo 2^48 */
  d->status[0] = STATS_SENTINEL_OK;
  d->life_enabled_ns = 2000;
  d->life_running_ns = 1000;
  d->time_status = STATS_SENTINEL_OK;
  publish(&u);
  s = get_current_stats(&g_type, "mbox3");
  assert(state_of(s, "cas_reads") == STATS_SENTINEL_OK);
  assert(s->s_val[schema_ref(&g_type.st_schema, "cas_reads")] == 7ULL);
  assert(state_of(s, "cas_writes") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "act_count") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "pmu_running_time") == STATS_SENTINEL_OK);

  d->status[0] = STATS_SENTINEL_READ_FAILED;
  d->time_status = STATS_SENTINEL_PMU_BUSY;
  publish(&u);
  assert(state_of(s, "cas_reads") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(s, "pmu_enabled_time") == STATS_SENTINEL_PMU_BUSY);
  assert(state_of(s, "pmu_running_time") == STATS_SENTINEL_PMU_BUSY);
}

int main(void)
{
  memset(&g_type, 0, sizeof(g_type));
  snprintf(g_type.st_name, sizeof(g_type.st_name), "%s", "intel_x86_uncore_imc_test");
  g_type.st_schema_def = "cas_reads,E,W=48 cas_writes,E,W=48 act_count,E,W=48 "
                         "pmu_enabled_time,E,W=64 pmu_running_time,E,W=64";
  assert(stats_type_init(&g_type) == 0);
  g_type.st_enabled = 1;
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);

  test_publish_codes();

  stats_type_destroy(&g_type);
  printf("test_fault_inject_x86_uncore passed\n");
  return 0;
}
