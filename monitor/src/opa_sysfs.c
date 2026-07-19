/* host_opa sysfs fallback — map hfi1 ports/N/counters names to schema KEYS. */
#include <stdio.h>
#include <string.h>

#include "opa_sysfs.h"
#include "pscanf.h"
#include "stats.h"

struct opa_sysfs_map_entry {
  const char *sysfs_name;
  const char *schema_key;
};

/* Verified on Stampede3 OPA100 (Intel HFI 100 Series) and Cornelis CN5000:
 * IB-style filenames under ports/N/counters; schema uses *_pkts not *_packets.
 * CN5000 may omit multicast_* files — collect skips missing paths. Do not map
 * HFI ports/N/hw_counters (DmaWait, …) into host_opa KEYS. */
static const struct opa_sysfs_map_entry opa_sysfs_map[] = {
  { "port_xmit_data", "port_xmit_data" },
  { "port_rcv_data", "port_rcv_data" },
  { "port_xmit_packets", "port_xmit_pkts" },
  { "port_rcv_packets", "port_rcv_pkts" },
  { "multicast_xmit_packets", "port_multicast_xmit_pkts" },
  { "multicast_rcv_packets", "port_multicast_rcv_pkts" },
  { "port_xmit_wait", "port_xmit_wait" },
  { NULL, NULL }
};

const char *opa_sysfs_schema_key_for_file(const char *sysfs_name)
{
  size_t i;

  if (sysfs_name == NULL || sysfs_name[0] == '\0')
    return NULL;
  for (i = 0; opa_sysfs_map[i].sysfs_name != NULL; i++) {
    if (strcmp(sysfs_name, opa_sysfs_map[i].sysfs_name) == 0)
      return opa_sysfs_map[i].schema_key;
  }
  return NULL;
}

int opa_sysfs_collect_port(struct stats *stats, const char *hca, int port)
{
  char path[192];
  size_t i;
  unsigned long long val;
  int n_ok = 0;

  if (stats == NULL || hca == NULL || port < 1)
    return -1;

  for (i = 0; opa_sysfs_map[i].sysfs_name != NULL; i++) {
    snprintf(path, sizeof(path),
             "/sys/class/infiniband/%s/ports/%d/counters/%s",
             hca, port, opa_sysfs_map[i].sysfs_name);
    if (pscanf(path, "%llu", &val) != 1)
      continue;
    stats_set(stats, opa_sysfs_map[i].schema_key, val);
    n_ok++;
  }
  return n_ok > 0 ? 0 : -1;
}
