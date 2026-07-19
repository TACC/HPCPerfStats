/* opa_sysfs schema key mapping for hfi1 ports/N/counters filenames. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "opa_sysfs.h"
#include "stats.h"

/* Satisfy link of opa_sysfs.c (collect path unused by this driver). */
void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  (void) stats;
  (void) key;
  (void) val;
}

static void test_mapped_keys(void)
{
  assert(strcmp(opa_sysfs_schema_key_for_file("port_xmit_data"), "port_xmit_data") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_rcv_data"), "port_rcv_data") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_xmit_packets"), "port_xmit_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_rcv_packets"), "port_rcv_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("multicast_xmit_packets"),
                 "port_multicast_xmit_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("multicast_rcv_packets"),
                 "port_multicast_rcv_pkts") == 0);
  assert(strcmp(opa_sysfs_schema_key_for_file("port_xmit_wait"), "port_xmit_wait") == 0);
}

static void test_unmapped_and_null(void)
{
  assert(opa_sysfs_schema_key_for_file("port_rcv_fecn") == NULL);
  assert(opa_sysfs_schema_key_for_file("DmaWait") == NULL);
  assert(opa_sysfs_schema_key_for_file(NULL) == NULL);
  assert(opa_sysfs_schema_key_for_file("") == NULL);
}

int main(void)
{
  test_mapped_keys();
  test_unmapped_and_null();
  printf("test_opa_sysfs_map passed\n");
  return 0;
}
