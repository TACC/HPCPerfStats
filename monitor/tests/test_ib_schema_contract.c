#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "ib.h"

#define X SCHEMA_DEF
static const char ib_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

int main(void)
{
  assert_present(ib_schema_def, " port_rcv_data,E,W=32,U=4B");
  assert_present(ib_schema_def, " port_xmit_data,E,W=32,U=4B");
  assert_present(ib_schema_def, " port_rcv_packets,E,W=32");
  assert_present(ib_schema_def, " port_xmit_packets,E,W=32");
  assert_present(ib_schema_def, " link_downed,E,W=32");
  assert_present(ib_schema_def, " port_xmit_wait,E,W=32,U=ms");
  assert_present(ib_schema_def, " sw_rx_bytes,E,U=4B");
  assert_present(ib_schema_def, " sw_tx_bytes,E,U=4B");
  assert_present(ib_schema_def, " port_xmit_pkts,E");
  assert_present(ib_schema_def, " port_rcv_pkts,E");
  printf("test_ib_schema_contract passed\n");
  return 0;
}
