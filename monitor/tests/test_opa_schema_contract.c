#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "host_opa.h"

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
