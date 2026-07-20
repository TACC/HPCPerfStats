/* ib_mad_ext_decode_counters: decode via hooks (works with IB MAD dlopen builds). */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <infiniband/mad.h>

#include "ib_mad.h"
#include "stats.h"
#include "test_stats_stub.h"

#if defined(MONITOR_IB_MAD_DLOPEN)
#include "ib_mad_dyn.h"
#endif

static struct test_stats_stub g_stub;

#if defined(MONITOR_IB_MAD_DLOPEN)
static uint64_t g_xmt_bytes;
static uint64_t g_rcv_bytes;
static uint64_t g_xmt_pkts;
static uint64_t g_rcv_pkts;

static void fake_mad_decode_field(uint8_t *buf, enum MAD_FIELDS field, void *val)
{
  (void)buf;
  if (val == NULL)
    return;
  switch (field) {
  case IB_PC_EXT_XMT_BYTES_F:
    *(uint64_t *)val = g_xmt_bytes;
    break;
  case IB_PC_EXT_RCV_BYTES_F:
    *(uint64_t *)val = g_rcv_bytes;
    break;
  case IB_PC_EXT_XMT_PKTS_F:
    *(uint64_t *)val = g_xmt_pkts;
    break;
  case IB_PC_EXT_RCV_PKTS_F:
    *(uint64_t *)val = g_rcv_pkts;
    break;
  default:
    memset(val, 0, sizeof(uint64_t));
    break;
  }
}
#endif

static void test_decode_populates_counter_fields(void)
{
  uint8_t mad_buf[1024];
  struct stats_type type;
  struct stats *stats;
  unsigned long long v;
#if defined(MONITOR_IB_MAD_DLOPEN)
  struct ib_mad_dyn_test_hooks hooks;
#endif

  test_stats_stub_reset(&g_stub);
  test_stats_stub_bind(&g_stub);
  memset(&type, 0, sizeof(type));
  stats = malloc(sizeof(*stats) + strlen("mlx5_0.1") + 1);
  assert(stats != NULL);
  memset(stats, 0, sizeof(*stats));
  stats->s_type = &type;
  strcpy(stats->s_dev, "mlx5_0.1");

  memset(mad_buf, 0, sizeof(mad_buf));
#if defined(MONITOR_IB_MAD_DLOPEN)
  g_xmt_bytes = 1000ULL;
  g_rcv_bytes = 2000ULL;
  g_xmt_pkts = 10ULL;
  g_rcv_pkts = 20ULL;
  memset(&hooks, 0, sizeof(hooks));
  hooks.mad_decode_field = fake_mad_decode_field;
  ib_mad_dyn_test_set_hooks(&hooks);
#else
  {
    uint64_t xmt_bytes = 1000ULL;
    uint64_t rcv_bytes = 2000ULL;
    uint64_t xmt_pkts = 10ULL;
    uint64_t rcv_pkts = 20ULL;

    mad_encode_field(mad_buf, IB_PC_EXT_XMT_BYTES_F, &xmt_bytes);
    mad_encode_field(mad_buf, IB_PC_EXT_RCV_BYTES_F, &rcv_bytes);
    mad_encode_field(mad_buf, IB_PC_EXT_XMT_PKTS_F, &xmt_pkts);
    mad_encode_field(mad_buf, IB_PC_EXT_RCV_PKTS_F, &rcv_pkts);
  }
#endif

  ib_mad_ext_decode_counters(stats, mad_buf);

  assert(test_stats_stub_find(&g_stub, "port_xmit_data", &v) == 1);
  assert(v == 1000ULL);
  assert(test_stats_stub_find(&g_stub, "port_rcv_data", &v) == 1);
  assert(v == 2000ULL);
  assert(test_stats_stub_find(&g_stub, "port_xmit_pkts", &v) == 1);
  assert(v == 10ULL);
  assert(test_stats_stub_find(&g_stub, "port_rcv_pkts", &v) == 1);
  assert(v == 20ULL);

  /* NULL guards */
  ib_mad_ext_decode_counters(NULL, mad_buf);
  ib_mad_ext_decode_counters(stats, NULL);

#if defined(MONITOR_IB_MAD_DLOPEN)
  ib_mad_dyn_test_set_hooks(NULL);
#endif
  test_stats_stub_unbind();
  free(stats);
}

int main(void)
{
  test_decode_populates_counter_fields();
  printf("test_ib_mad_decode passed\n");
  return 0;
}
