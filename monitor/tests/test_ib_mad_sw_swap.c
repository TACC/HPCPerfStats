/* Switch MAD TX/RX perspective swap publishes host-centric sw_* keys. */
#include <assert.h>
#include <stdio.h>

#include "ib_mad.h"
#include "test_stats_stub.h"

static struct stats g_dummy_stats;

static void test_sw_publish_tx_rx_swap(void)
{
  struct test_stats_stub stub;
  unsigned long long val;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  /* Switch sees rx=100 from host tx; host tx bytes on switch rx side. */
  ib_mad_sw_publish_tx_rx_swap(&g_dummy_stats, 100ULL, 10ULL, 200ULL, 20ULL);

  assert(test_stats_stub_find(&stub, "sw_rx_bytes", &val) && val == 200ULL);
  assert(test_stats_stub_find(&stub, "sw_rx_packets", &val) && val == 20ULL);
  assert(test_stats_stub_find(&stub, "sw_tx_bytes", &val) && val == 100ULL);
  assert(test_stats_stub_find(&stub, "sw_tx_packets", &val) && val == 10ULL);

  test_stats_stub_unbind();
}

int main(void)
{
  test_sw_publish_tx_rx_swap();
  printf("test_ib_mad_sw_swap passed\n");
  return 0;
}
