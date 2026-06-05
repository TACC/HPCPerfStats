#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"

/* host_opa KEYS from opa.c */
#define KEYS \
  X(port_xmit_data, "E", ""), \
  X(port_rcv_data, "E", ""), \
  X(port_xmit_pkts, "E", ""), \
  X(port_rcv_pkts, "E", ""), \
  X(port_multicast_xmit_pkts, "E", ""), \
  X(port_multicast_rcv_pkts, "E", ""), \
  X(port_xmit_wait, "E", ""), \
  X(sw_port_congestion, "E", ""), \
  X(port_rcv_fecn, "E", ""), \
  X(port_rcv_becn, "E", ""), \
  X(port_xmit_time_cong, "E", ""), \
  X(port_xmit_wasted_bw, "E", ""), \
  X(port_xmit_wait_data, "E", ""), \
  X(port_rcv_bubble, "E", ""), \
  X(port_mark_fecn, "E", ""), \
  X(port_error_counter_summary, "E", "")
#define X SCHEMA_DEF
static const char opa_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

int main(void)
{
  assert_present(opa_schema_def, " port_xmit_data,E");
  assert_present(opa_schema_def, " port_rcv_data,E");
  assert_present(opa_schema_def, " port_xmit_pkts,E");
  assert_present(opa_schema_def, " port_rcv_pkts,E");
  assert_present(opa_schema_def, " sw_port_congestion,E");
  assert_present(opa_schema_def, " port_rcv_fecn,E");
  assert_present(opa_schema_def, " port_error_counter_summary,E");
  printf("test_opa_schema_contract passed\n");
  return 0;
}
