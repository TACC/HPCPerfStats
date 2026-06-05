/* ib_mad_ext_decode_counters: decode synthetic PMA extended counter MAD buffer. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <infiniband/mad.h>

#include "ib_mad.h"
#include "stats.h"
#include "test_stats_stub.h"

static struct test_stats_stub g_stub;

static void test_decode_populates_counter_fields(void)
{
  uint8_t mad_buf[1024];
  struct stats_type type;
  struct stats *stats;
  unsigned long long v;

  test_stats_stub_reset(&g_stub);
  test_stats_stub_bind(&g_stub);
  memset(&type, 0, sizeof(type));
  stats = malloc(sizeof(*stats) + strlen("mlx5_0.1") + 1);
  assert(stats != NULL);
  memset(stats, 0, sizeof(*stats));
  stats->s_type = &type;
  strcpy(stats->s_dev, "mlx5_0.1");

  uint64_t xmt_bytes = 1000ULL;
  uint64_t rcv_bytes = 2000ULL;
  uint64_t xmt_pkts = 10ULL;
  uint64_t rcv_pkts = 20ULL;

  memset(mad_buf, 0, sizeof(mad_buf));
  mad_encode_field(mad_buf, IB_PC_EXT_XMT_BYTES_F, &xmt_bytes);
  mad_encode_field(mad_buf, IB_PC_EXT_RCV_BYTES_F, &rcv_bytes);
  mad_encode_field(mad_buf, IB_PC_EXT_XMT_PKTS_F, &xmt_pkts);
  mad_encode_field(mad_buf, IB_PC_EXT_RCV_PKTS_F, &rcv_pkts);

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

  test_stats_stub_unbind();
  free(stats);
}

int main(void)
{
  test_decode_populates_counter_fields();
  printf("test_ib_mad_decode passed\n");
  return 0;
}
