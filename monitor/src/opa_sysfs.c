/* host_opa sysfs fallback — map hfi1 ports/N/counters (+ hw_counters) to KEYS. */
#include <stdio.h>
#include <string.h>

#include "opa_sysfs.h"
#include "pscanf.h"
#include "stats.h"

struct opa_sysfs_map_entry {
  const char *sysfs_name;
  const char *schema_key;
};

/*
 * Classic IB-style filenames under ports/N/counters (Stampede3 OPA100).
 * Schema uses *_pkts not *_packets. Multicast files may be absent — skipped.
 * On Cornelis CN5000 NDR, these files often exist but read EINVAL; then
 * opa_sysfs_collect_port falls back to hw_counters (see opa_sysfs_hw_map).
 */
static const struct opa_sysfs_map_entry opa_sysfs_map[] = {
    {"port_xmit_data", "port_xmit_data"},
    {"port_rcv_data", "port_rcv_data"},
    {"port_xmit_packets", "port_xmit_pkts"},
    {"port_rcv_packets", "port_rcv_pkts"},
    {"multicast_xmit_packets", "port_multicast_xmit_pkts"},
    {"multicast_rcv_packets", "port_multicast_rcv_pkts"},
    {"port_xmit_wait", "port_xmit_wait"},
    {NULL, NULL}};

/*
 * CN5000 NDR utilization under ports/N/hw_counters (verified c512-122).
 * Do not map DmaWait / RcvHdrOvr* / comma-named files into KEYS (v1).
 */
static const struct opa_sysfs_map_entry opa_sysfs_hw_map[] = {
    {"TxWords", "port_xmit_data"}, {"RxWords", "port_rcv_data"}, {"TxPkt", "port_xmit_pkts"},
    {"RxPkt", "port_rcv_pkts"},    {"TxWait", "port_xmit_wait"}, {NULL, NULL}};

static const char *g_opa_sysfs_root = "/sys";

void opa_sysfs_test_set_root(const char *root)
{
  g_opa_sysfs_root = (root != NULL && root[0] != '\0') ? root : "/sys";
}

static const char *lookup_map(const struct opa_sysfs_map_entry *table, const char *sysfs_name)
{
  size_t i;

  if (sysfs_name == NULL || sysfs_name[0] == '\0')
    return NULL;
  for (i = 0; table[i].sysfs_name != NULL; i++) {
    if (strcmp(sysfs_name, table[i].sysfs_name) == 0)
      return table[i].schema_key;
  }
  return NULL;
}

const char *opa_sysfs_schema_key_for_file(const char *sysfs_name)
{
  return lookup_map(opa_sysfs_map, sysfs_name);
}

const char *opa_sysfs_hw_schema_key_for_file(const char *sysfs_name)
{
  return lookup_map(opa_sysfs_hw_map, sysfs_name);
}

static int collect_from_map(struct stats *stats, const char *hca, int port, const char *subdir,
                            const struct opa_sysfs_map_entry *table)
{
  char path[256];
  size_t i;
  unsigned long long val;
  int n_ok = 0;

  if (stats == NULL || hca == NULL || port < 1 || subdir == NULL || table == NULL)
    return -1;

  for (i = 0; table[i].sysfs_name != NULL; i++) {
    snprintf(path, sizeof(path), "%s/class/infiniband/%s/ports/%d/%s/%s", g_opa_sysfs_root, hca,
             port, subdir, table[i].sysfs_name);
    if (pscanf(path, "%llu", &val) != 1)
      continue;
    stats_set(stats, table[i].schema_key, val);
    n_ok++;
  }
  return n_ok > 0 ? 0 : -1;
}

int opa_sysfs_collect_classic_counters(struct stats *stats, const char *hca, int port)
{
  return collect_from_map(stats, hca, port, "counters", opa_sysfs_map);
}

int opa_sysfs_collect_hw_counters(struct stats *stats, const char *hca, int port)
{
  return collect_from_map(stats, hca, port, "hw_counters", opa_sysfs_hw_map);
}

int opa_sysfs_collect_port(struct stats *stats, const char *hca, int port)
{
  if (opa_sysfs_collect_classic_counters(stats, hca, port) == 0)
    return 0;
  /* Classic miss (missing files or EINVAL stubs): CN5000 NDR hw_counters. */
  return opa_sysfs_collect_hw_counters(stats, hca, port);
}
