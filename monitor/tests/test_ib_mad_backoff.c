/* MAD backoff and switch-port absence are tracked per HCA port. pscanf is stubbed to fail, so
 * every extended-counter attempt fails at the lid read without any MAD I/O. */
#include <assert.h>
#include <stdio.h>

#include "ib_mad.h"
#include "stats.h"
#if defined(MONITOR_IB_MAD_DLOPEN)
#include "ib_mad_dyn.h"
#endif

static char g_stats_buf[sizeof(struct stats) + 16];
static struct stats *g_s = (struct stats *)g_stats_buf;

static void fail_ext(const char *hca, int port, int n)
{
  for (int i = 0; i < n; i++)
    assert(ib_mad_ext_collect_port(g_s, hca, port) == STATS_SENTINEL_BACKEND_ERROR);
}

static void test_null_args(void)
{
  ib_mad_test_reset_backoff();
  assert(ib_mad_ext_collect_port(NULL, "mlx5_0", 1) == STATS_SENTINEL_BACKEND_ERROR);
  assert(ib_mad_ext_collect_port(g_s, NULL, 1) == STATS_SENTINEL_BACKEND_ERROR);
  assert(ib_mad_sw_collect_port(NULL, "mlx5_0", 1) == STATS_SENTINEL_BACKEND_ERROR);
  assert(ib_mad_sw_collect_port(g_s, NULL, 1) == STATS_SENTINEL_BACKEND_ERROR);
}

/* A persistently failing mlx5_4.1 backs off alone; other HCAs and ports keep collecting. */
static void test_ext_backoff_is_per_port(void)
{
  ib_mad_test_reset_backoff();
  fail_ext("mlx5_4", 1, 7);
  fail_ext("mlx5_0", 1, 1);
  fail_ext("mlx5_4", 1, 1);
  assert(ib_mad_ext_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_BACKOFF);
  fail_ext("mlx5_0", 1, 1);
  fail_ext("mlx5_4", 2, 1);
  assert(ib_mad_ext_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_BACKOFF);
}

/* After the cooldown one probe runs; when it fails the port backs off again at once. */
static void test_ext_backoff_probes_once_after_cooldown(void)
{
  ib_mad_test_reset_backoff();
  fail_ext("mlx5_4", 1, 8);
  assert(ib_mad_ext_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_BACKOFF);
  ib_mad_test_expire_backoff();
  fail_ext("mlx5_4", 1, 1);
  assert(ib_mad_ext_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_BACKOFF);
}

#if defined(MONITOR_IB_MAD_DLOPEN)
static int g_open_calls;
static int g_port_token;
static struct ibmad_port *g_open_result;

static struct ibmad_port *fake_open(char *dev, int port, int *classes, int n)
{
  (void)dev;
  (void)port;
  (void)classes;
  (void)n;
  g_open_calls++;
  return g_open_result;
}

static void fake_close(struct ibmad_port *p)
{
  (void)p;
}

static uint8_t *fake_smp(void *buf, ib_portid_t *id, unsigned attr, unsigned mod, unsigned timeout,
                         const struct ibmad_port *src)
{
  (void)buf;
  (void)id;
  (void)attr;
  (void)mod;
  (void)timeout;
  (void)src;
  return NULL;
}

/* A refused switch MAD open is probed once and stays NOT_PRESENT for that port only; a failed
 * switch query after a good open is transient and backs off per port. */
static void test_sw_open_refused_is_permanent_per_port(void)
{
  struct ib_mad_dyn_test_hooks hooks = {0};

  hooks.mad_rpc_open_port = fake_open;
  hooks.mad_rpc_close_port = fake_close;
  hooks.smp_query_via = fake_smp;
  ib_mad_dyn_test_set_hooks(&hooks);
  ib_mad_test_reset_backoff();
  g_open_calls = 0;

  g_open_result = NULL;
  assert(ib_mad_sw_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_NOT_PRESENT);
  assert(ib_mad_sw_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_NOT_PRESENT);
  ib_mad_test_expire_backoff();
  assert(ib_mad_sw_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_NOT_PRESENT);
  assert(g_open_calls == 1);

  g_open_result = (struct ibmad_port *)&g_port_token;
  for (int i = 0; i < 8; i++)
    assert(ib_mad_sw_collect_port(g_s, "mlx5_0", 1) == STATS_SENTINEL_BACKEND_ERROR);
  assert(ib_mad_sw_collect_port(g_s, "mlx5_0", 1) == STATS_SENTINEL_BACKOFF);
  assert(ib_mad_sw_collect_port(g_s, "mlx5_1", 1) == STATS_SENTINEL_BACKEND_ERROR);
  assert(ib_mad_sw_collect_port(g_s, "mlx5_4", 1) == STATS_SENTINEL_NOT_PRESENT);
  assert(g_open_calls == 10);

  ib_mad_dyn_test_set_hooks(NULL);
}
#endif

int main(void)
{
  test_null_args();
  test_ext_backoff_is_per_port();
  test_ext_backoff_probes_once_after_cooldown();
#if defined(MONITOR_IB_MAD_DLOPEN)
  test_sw_open_refused_is_permanent_per_port();
#endif
  printf("test_ib_mad_backoff passed\n");
  return 0;
}
