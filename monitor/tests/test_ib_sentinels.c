/* host_ib / host_opa value sentinels: sysfs NOT_PRESENT / READ_FAILED, MAD NOT_BUILT / BACKOFF /
 * BACKEND_ERROR, switch NOT_PRESENT, OPA STL-only keys without MAD. Real stats.c state bytes over a
 * fake sysfs root. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "collect_tier.h"
#include "ib_family.h"
#include "opa_sysfs.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"
#include "test_rm_tree.h"

#include "ib.h"
#define X SCHEMA_DEF
static char ib_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS
#include "host_opa.h"
#define X SCHEMA_DEF
static char opa_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
static struct stats_type g_ib;
static struct stats_type g_opa;
static char g_root[] = "/tmp/hps_ib_sentXXXXXX";

#if defined(MONITOR_WITH_INFINIBAND)
static enum stats_sentinel g_ext_code, g_sw_code;

enum stats_sentinel ib_mad_ext_collect_port(struct stats *stats, const char *hca, int port)
{
  (void)hca;
  (void)port;
  if (g_ext_code == STATS_SENTINEL_OK)
    stats_set(stats, "port_select", 1ULL);
  return g_ext_code;
}

enum stats_sentinel ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port)
{
  (void)hca;
  (void)port;
  if (g_sw_code == STATS_SENTINEL_OK)
    stats_set(stats, "sw_rx_bytes", 2ULL);
  return g_sw_code;
}
#endif

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

static struct stats *collect_ib(const char *hca, int port)
{
  char dev[80];

  snprintf(dev, sizeof(dev), "%s.%d", hca, port);
  stats_type_mark_not_updated(&g_ib);
  ib_family_collect_port(hca, port, &g_ib);
  return get_current_stats(&g_ib, dev);
}

/* A counter file that exists reads OK, a schema key without a file is NOT_PRESENT, a file that
 * does not parse is READ_FAILED, and an unreadable counters directory is READ_FAILED. */
static void test_ib_sysfs_codes(void)
{
  struct stats *s;

  mk("class/infiniband/mlx5_0");
  mk("class/infiniband/mlx5_0/ports");
  mk("class/infiniband/mlx5_0/ports/1");
  mk("class/infiniband/mlx5_0/ports/1/counters");
  put("class/infiniband/mlx5_0/ports/1/counters/port_rcv_data", "123\n");
  put("class/infiniband/mlx5_0/ports/1/counters/symbol_error", "junk\n");

  s = collect_ib("mlx5_0", 1);
  assert(state_of(s, "port_rcv_data") == STATS_SENTINEL_OK);
  assert(s->s_val[schema_ref(&g_ib.st_schema, "port_rcv_data")] == 123ULL);
  assert(state_of(s, "symbol_error") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(s, "link_downed") == STATS_SENTINEL_NOT_PRESENT);

  /* counters is a regular file: opendir fails ENOTDIR, the whole port is READ_FAILED. */
  mk("class/infiniband/mlx5_1");
  mk("class/infiniband/mlx5_1/ports");
  mk("class/infiniband/mlx5_1/ports/1");
  put("class/infiniband/mlx5_1/ports/1/counters", "x\n");
  s = collect_ib("mlx5_1", 1);
  assert(state_of(s, "port_rcv_data") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(s, "link_downed") == STATS_SENTINEL_READ_FAILED);
}

static void test_ib_mad_codes(void)
{
  struct stats *s;

#if defined(MONITOR_WITH_INFINIBAND)
  g_ext_code = STATS_SENTINEL_OK;
  g_sw_code = STATS_SENTINEL_BACKEND_ERROR;
  s = collect_ib("mlx5_0", 1);
  assert(state_of(s, "port_select") == STATS_SENTINEL_OK);
  assert(state_of(s, "port_xmit_pkts") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "sw_rx_bytes") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state_of(s, "sw_tx_packets") == STATS_SENTINEL_BACKEND_ERROR);

  g_ext_code = STATS_SENTINEL_BACKEND_ERROR;
  g_sw_code = STATS_SENTINEL_BACKOFF;
  s = collect_ib("mlx5_0", 1);
  assert(state_of(s, "port_select") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state_of(s, "port_multicast_rcv_pkts") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state_of(s, "sw_rx_bytes") == STATS_SENTINEL_BACKOFF);
  /* sysfs-backed keys keep their sysfs reading when MAD fails. */
  assert(state_of(s, "port_rcv_data") == STATS_SENTINEL_OK);

  g_ext_code = STATS_SENTINEL_BACKOFF;
  g_sw_code = STATS_SENTINEL_OK;
  s = collect_ib("mlx5_0", 1);
  assert(state_of(s, "counter_select") == STATS_SENTINEL_BACKOFF);
  assert(state_of(s, "sw_rx_bytes") == STATS_SENTINEL_OK);

  /* A port whose switch MAD open was refused: sw_* NOT_PRESENT, extended counters still read. */
  g_ext_code = STATS_SENTINEL_OK;
  g_sw_code = STATS_SENTINEL_NOT_PRESENT;
  s = collect_ib("mlx5_0", 1);
  assert(state_of(s, "sw_rx_bytes") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "sw_tx_packets") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "port_select") == STATS_SENTINEL_OK);
#else
  s = collect_ib("mlx5_0", 1);
  assert(state_of(s, "port_select") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "sw_rx_bytes") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "port_rcv_data") == STATS_SENTINEL_OK);
#endif

  /* The marker itself, as the non-MAD build applies it. */
  ib_family_mark_mad_keys(s, 1, STATS_SENTINEL_NOT_BUILT);
  ib_family_mark_mad_keys(s, 0, STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "sw_tx_bytes") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "port_unicast_xmit_pkts") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "port_rcv_data") == STATS_SENTINEL_OK);
}

/* host_opa without STL MAD: sysfs keys read, absent files NOT_PRESENT, unparsable READ_FAILED,
 * and every STL-only key gets the MAD code. */
static void test_opa_codes_without_mad(void)
{
  struct stats *s = get_current_stats(&g_opa, "hfi1_0/1");

  mk("class/infiniband/hfi1_0");
  mk("class/infiniband/hfi1_0/ports");
  mk("class/infiniband/hfi1_0/ports/1");
  mk("class/infiniband/hfi1_0/ports/1/counters");
  put("class/infiniband/hfi1_0/ports/1/counters/port_xmit_data", "77\n");
  put("class/infiniband/hfi1_0/ports/1/counters/port_rcv_data", "bad\n");

  opa_sysfs_test_set_root(g_root);
  stats_type_mark_not_updated(&g_opa);
  assert(opa_sysfs_collect_port_without_mad(s, "hfi1_0", 1, STATS_SENTINEL_NOT_BUILT) == 0);
  assert(state_of(s, "port_xmit_data") == STATS_SENTINEL_OK);
  assert(state_of(s, "port_rcv_data") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(s, "port_multicast_xmit_pkts") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "sw_port_congestion") == STATS_SENTINEL_NOT_BUILT);
  assert(state_of(s, "port_rcv_fecn") == STATS_SENTINEL_NOT_BUILT);

  stats_type_mark_not_updated(&g_opa);
  (void)opa_sysfs_collect_port_without_mad(s, "hfi1_0", 1, STATS_SENTINEL_BACKOFF);
  assert(state_of(s, "port_xmit_wasted_bw") == STATS_SENTINEL_BACKOFF);
  stats_type_mark_not_updated(&g_opa);
  (void)opa_sysfs_collect_port_without_mad(s, "hfi1_0", 1, STATS_SENTINEL_BACKEND_ERROR);
  assert(state_of(s, "port_mark_fecn") == STATS_SENTINEL_BACKEND_ERROR);
  assert(state_of(s, "port_xmit_data") == STATS_SENTINEL_OK);
  opa_sysfs_test_set_root(NULL);
}

static void init_type(struct stats_type *t, const char *name, char *def)
{
  memset(t, 0, sizeof(*t));
  snprintf(t->st_name, sizeof(t->st_name), "%s", name);
  t->st_schema_def = def;
  assert(stats_type_init(t) == 0);
}

int main(void)
{
  assert(mkdtemp(g_root) != NULL);
  mk("class");
  mk("class/infiniband");
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);
  init_type(&g_ib, "host_ib", ib_schema_def);
  init_type(&g_opa, "host_opa", opa_schema_def);
  ib_family_test_set_sysfs_root(g_root);

  test_ib_sysfs_codes();
  test_ib_mad_codes();
  test_opa_codes_without_mad();

  ib_family_test_set_sysfs_root(NULL);
  stats_type_destroy(&g_ib);
  stats_type_destroy(&g_opa);
  assert(test_rm_tree(g_root) == 0);
  printf("test_ib_sentinels passed\n");
  return 0;
}
