#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <ctype.h>
#include <stdint.h>
#include <infiniband/umad.h>
#include <infiniband/mad.h>
#include "stats.h"
#include "trace.h"
#include "pscanf.h"

/* CHECKME Is unit 4B for extended counters as well? */

#define KEYS \
  X(port_select, "C", ""), \
  X(counter_select, "C", ""), \
  X(port_xmit_data, "E,U=4B", ""), \
  X(port_rcv_data, "E,U=4B", ""), \
  X(port_xmit_pkts, "E", ""), \
  X(port_rcv_pkts, "E", ""), \
  X(port_unicast_xmit_pkts, "E", ""), \
  X(port_unicast_rcv_pkts, "E", ""), \
  X(port_multicast_xmit_pkts, "E", ""), \
  X(port_multicast_rcv_pkts, "E", "")

#define IB_PC_EXT_F \
  X(uint32_t, port_select, IB_PC_EXT_PORT_SELECT_F) \
  X(uint32_t, counter_select, IB_PC_EXT_COUNTER_SELECT_F) \
  X(uint64_t, port_xmit_data, IB_PC_EXT_XMT_BYTES_F) \
  X(uint64_t, port_rcv_data, IB_PC_EXT_RCV_BYTES_F) \
  X(uint64_t, port_xmit_pkts, IB_PC_EXT_XMT_PKTS_F) \
  X(uint64_t, port_rcv_pkts, IB_PC_EXT_RCV_PKTS_F) \
  X(uint64_t, port_unicast_xmit_pkts, IB_PC_EXT_XMT_UPKTS_F) \
  X(uint64_t, port_unicast_rcv_pkts, IB_PC_EXT_RCV_UPKTS_F) \
  X(uint64_t, port_multicast_xmit_pkts, IB_PC_EXT_XMT_MPKTS_F) \
  X(uint64_t, port_multicast_rcv_pkts, IB_PC_EXT_RCV_MPKTS_F)

static void collect_lid_port(struct stats *stats, char* hca, int lid, int port)
{
  struct ibmad_port *mad_port = NULL;
  int mgmt_class = IB_PERFORMANCE_CLASS;
  ib_portid_t portid = { .lid = lid };
  uint8_t mad_buf[1024];
  int timeout = 0;

  mad_port = mad_rpc_open_port(hca, port, &mgmt_class, 1);
  if (mad_port == NULL) {
    ERROR("cannot open mad rpc port: %m\n");
    goto out;
  }

  memset(mad_buf, 0, sizeof(mad_buf));

  if (pma_query_via(mad_buf, &portid, port, timeout, IB_GSI_PORT_COUNTERS_EXT, mad_port) == NULL) {
    ERROR("cannot query performance counters: %m\n");
    goto out;
  }

#define X(t, m, f)                    \
  do {                                \
    t m;                              \
    mad_decode_field(mad_buf, f, &m); \
    stats_set(stats, #m, m);          \
  } while (0);
  IB_PC_EXT_F;
#undef X

 out:
  if (mad_port != NULL)
    mad_rpc_close_port(mad_port);
}

static void collect_hca_port(struct stats_type *type, char *hca, int port)
{
  struct stats *stats = NULL;
  char dev[80];
  char path[80];
  int state = -1;
  unsigned int lid = -1;

  /* Check that device is active. .../state should read "4: ACTIVE." */
  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/state", hca, port);
  if (pscanf(path, "%d", &state) != 1) {
    ERROR("cannot read state of IB HCA `%s' port %d: %m\n", hca, port);
    goto out;
  }

  if (state != 4) {
    TRACE("skipping inactive IB HCA `%s', port %d, state %d\n", hca, port, state);
    goto out;
  }

  /* Get the lid. */
  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%i/lid", hca, port);
  if (pscanf(path, "%x", &lid) != 1) {
    ERROR("cannot read lid of IB HCA `%s' port %d: %m\n", hca, port);
    goto out;
  }

  TRACE("IB HCA %s, port %d, lid %x, state %d\n", hca, port, lid, state);

  snprintf(dev, sizeof(dev), "%s/%d", hca, port);
  stats = get_current_stats(type, dev);
  if (stats == NULL)
    goto out;

  collect_lid_port(stats, hca, lid, port);

 out:
  (void) 0;
}

static void collect_ib_ext(struct stats_type *type)
{
  const char *sys_path = "/sys/class/infiniband";
  DIR *sys_dir = NULL;

  sys_dir = opendir(sys_path);
  if (sys_dir == NULL) {
    ERROR("cannot open `%s': %m\n", sys_path);
    goto out;
  }

  struct dirent *sys_ent;
  while ((sys_ent = readdir(sys_dir)) != NULL) {
    char ports_path[80];
    DIR *ports_dir = NULL;
    char *hca = sys_ent->d_name;
    struct dirent *ent;

    if (hca[0] == '.')
      goto next;

    snprintf(ports_path, sizeof(ports_path), "%s/%s/ports", sys_path, hca);
    ports_dir = opendir(ports_path);
    if (ports_dir == NULL) {
      ERROR("cannot open `%s': %m\n", ports_path);
      goto next;
    }

    while ((ent = readdir(ports_dir)) != NULL) {
      if (isdigit(ent->d_name[0]))
        collect_hca_port(type, hca, atoi(ent->d_name));
    }

  next:
    if (ports_dir != NULL)
      closedir(ports_dir);
  }

 out:
  if (sys_dir != NULL)
    closedir(sys_dir);
}

struct stats_type ib_ext_stats_type = {
  .st_name = "ib_ext",
  .st_collect = &collect_ib_ext,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
