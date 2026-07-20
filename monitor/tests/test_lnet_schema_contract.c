#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"

/* host_lnet KEYS from lnet.c */
#define KEYS                                                                                       \
  X(msgs_alloc, "E", ""), X(msgs_alloc_max, "E", ""), X(errors, "E", ""), X(tx_msgs, "E", ""),     \
      X(rx_msgs, "E", ""), X(route_msgs, "E", ""), X(rx_msgs_dropped, "E", ""),                    \
      X(tx_bytes, "E,U=B", ""), X(rx_bytes, "E,U=B", ""), X(route_bytes, "E,U=B", ""),             \
      X(rx_bytes_dropped, "E,U=B", "")
#define X SCHEMA_DEF
static const char lnet_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

int main(void)
{
  assert_present(lnet_schema_def, " msgs_alloc,E");
  assert_present(lnet_schema_def, " tx_msgs,E");
  assert_present(lnet_schema_def, " rx_msgs,E");
  assert_present(lnet_schema_def, " tx_bytes,E,U=B");
  assert_present(lnet_schema_def, " rx_bytes,E,U=B");
  assert_present(lnet_schema_def, " route_bytes,E,U=B");
  printf("test_lnet_schema_contract passed\n");
  return 0;
}
