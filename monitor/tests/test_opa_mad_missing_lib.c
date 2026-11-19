/* On a MAD-dlopen build whose liboib_utils is missing at runtime, host_opa STL-only keys emit
 * NOT_PRESENT, never NOT_BUILT, and the sysfs keys still read. Includes opa.c to reach the
 * static per-port collect over a fake sysfs root. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "collect_tier.h"
#include "stats_sentinel.h"
#include "test_rm_tree.h"

#include "opa.c"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
static char g_root[] = "/tmp/hps_opa_nolibXXXXXX";

static const char *const k_mad_keys[] = {
    "port_error_counter_summary",
    "port_mark_fecn",
    "port_rcv_becn",
    "port_rcv_bubble",
    "port_rcv_fecn",
    "port_xmit_time_cong",
    "port_xmit_wait_data",
    "port_xmit_wasted_bw",
    "sw_port_congestion",
};

/* sysfs counters/ file name, schema key */
static const char *const k_sysfs_keys[][2] = {
    {"port_xmit_data", "port_xmit_data"},
    {"port_rcv_data", "port_rcv_data"},
    {"port_xmit_packets", "port_xmit_pkts"},
    {"port_rcv_packets", "port_rcv_pkts"},
    {"multicast_xmit_packets", "port_multicast_xmit_pkts"},
    {"multicast_rcv_packets", "port_multicast_rcv_pkts"},
    {"port_xmit_wait", "port_xmit_wait"},
};

static unsigned int state_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static void put(const char *rel, const char *content)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", g_root, rel);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(content, f);
  fclose(f);
}

static void mk(const char *rel)
{
  char path[512];

  snprintf(path, sizeof(path), "%s/%s", g_root, rel);
  assert(mkdir(path, 0700) == 0);
}

static void test_opa_missing_oib_lib_not_present(void)
{
  struct stats *s;
  char rel[128];
  size_t i;

  mk("class");
  mk("class/infiniband");
  mk("class/infiniband/hfi1_0");
  mk("class/infiniband/hfi1_0/ports");
  mk("class/infiniband/hfi1_0/ports/1");
  mk("class/infiniband/hfi1_0/ports/1/counters");
  for (i = 0; i < sizeof(k_sysfs_keys) / sizeof(k_sysfs_keys[0]); i++) {
    snprintf(rel, sizeof(rel), "class/infiniband/hfi1_0/ports/1/counters/%s", k_sysfs_keys[i][0]);
    put(rel, "41\n");
  }
  opa_sysfs_test_set_root(g_root);
  opa_mad_dyn_unload();
  assert(setenv("HPCPERFSTATS_OIB_LIB", "/nonexistent/liboib_utils.so.test-missing", 1) == 0);
  s = get_current_stats(&opa_stats_type, "hfi1_0/1");
  assert(s != NULL);
  stats_type_mark_not_updated(&opa_stats_type);

  opa_collect_one_port(s, "hfi1_0", 1);

  assert(opa_mad_dyn_available() == 0);
  for (i = 0; i < sizeof(k_mad_keys) / sizeof(k_mad_keys[0]); i++)
    assert(state_of(s, k_mad_keys[i]) == STATS_SENTINEL_NOT_PRESENT);
  for (i = 0; i < sizeof(k_sysfs_keys) / sizeof(k_sysfs_keys[0]); i++)
    assert(state_of(s, k_sysfs_keys[i][1]) == STATS_SENTINEL_OK);
  unsetenv("HPCPERFSTATS_OIB_LIB");
  opa_mad_dyn_unload();
  opa_sysfs_test_set_root(NULL);
}

int main(void)
{
  assert(mkdtemp(g_root) != NULL);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);
  assert(stats_type_init(&opa_stats_type) == 0);

  test_opa_missing_oib_lib_not_present();

  stats_type_destroy(&opa_stats_type);
  assert(test_rm_tree(g_root) == 0);
  printf("test_opa_mad_missing_lib passed\n");
  return 0;
}
