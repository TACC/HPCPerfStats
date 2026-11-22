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

/* Schema entries are " key,options": match the whole key, not a substring of another. */
static void assert_absent_key(const char *schema, const char *key)
{
  char frag[64];

  snprintf(frag, sizeof(frag), " %s,", key);
  assert(strstr(schema, frag) == NULL);
}

int main(void)
{
  /* Data and packet counters come from the kernel's 64-bit sysfs counters: declaring W=32 made a
   * counter reset look like a wrap. */
  assert_present(ib_schema_def, " port_rcv_data,E,W=64,U=4B");
  assert_present(ib_schema_def, " port_xmit_data,E,W=64,U=4B");
  assert_present(ib_schema_def, " port_rcv_packets,E,W=64");
  assert_present(ib_schema_def, " port_xmit_packets,E,W=64");
  assert_present(ib_schema_def, " link_downed,E,W=32");
  assert_present(ib_schema_def, " port_xmit_wait,E,W=32,U=ms");
  assert_present(ib_schema_def, " sw_rx_bytes,E,U=4B");
  assert_present(ib_schema_def, " sw_rx_packets,E");
  assert_present(ib_schema_def, " sw_tx_bytes,E,U=4B");
  assert_present(ib_schema_def, " sw_tx_packets,E");
  assert(strstr(ib_schema_def, "vl15_dropped") == NULL);
  /* port_xmit_packets / port_rcv_packets (sysfs, same row) replace the retired MAD
   * PortCountersExtended packet keys; unicast / multicast and the selectors have no successor. */
  assert_absent_key(ib_schema_def, "port_select");
  assert_absent_key(ib_schema_def, "counter_select");
  assert_absent_key(ib_schema_def, "port_xmit_pkts");
  assert_absent_key(ib_schema_def, "port_rcv_pkts");
  assert_absent_key(ib_schema_def, "port_unicast_xmit_pkts");
  assert_absent_key(ib_schema_def, "port_unicast_rcv_pkts");
  assert_absent_key(ib_schema_def, "port_multicast_xmit_pkts");
  assert_absent_key(ib_schema_def, "port_multicast_rcv_pkts");
  printf("test_ib_schema_contract passed\n");
  return 0;
}
