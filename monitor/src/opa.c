/* host_opa — Intel Omni-Path (OPA) port counters via STL performance MADs. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <errno.h>
#include "pscanf.h"
#include "stats.h"
#include "trace.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"
#include "oib_utils.h"
#include "iba/stl_pa.h"
#include "iba/stl_sm.h"

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

uint64_t g_transactID = 0xffffffff12340000;
#define RESP_WAIT_TIME 1000

static int opa_count_ports(uint64 portmask)
{
  int i;
  int nports = 0;

  for (i = 0; i < MAX_PM_PORTS; i++) {
    if ((portmask >> i) & (uint64) 1)
      nports++;
  }
  return nports;
}

static void opa_init_port_counters_mad(STL_PERF_MAD *mad, uint32_t port)
{
  STL_DATA_PORT_COUNTERS_REQ *req = (STL_DATA_PORT_COUNTERS_REQ *) &mad->PerfData;
  uint64 portmask = (uint64) 1 << port;
  uint32 attrmod;

  MemoryClear(mad, sizeof(*mad));
  req->PortSelectMask[3] = portmask;
  req->VLSelectMask = 0x1;
  attrmod = (uint32) opa_count_ports(portmask) << 24;
  BSWAP_STL_DATA_PORT_COUNTERS_REQ(req);

  mad->common.BaseVersion = STL_BASE_VERSION;
  mad->common.ClassVersion = STL_PM_CLASS_VERSION;
  mad->common.MgmtClass = MCLASS_PERF;
  mad->common.u.NS.Status.AsReg16 = 0;
  mad->common.mr.AsReg8 = 0;
  mad->common.mr.s.Method = MMTHD_GET;
  mad->common.AttributeID = STL_PM_ATTRIB_ID_DATA_PORT_COUNTERS;
  mad->common.TransactionID = (++g_transactID);
  mad->common.AttributeModifier = attrmod;
}

static int opa_send_port_counters_mad(struct oib_port *mad_port, STL_PERF_MAD *mad, IB_LID lid)
{
  uint16_t pkey;
  struct oib_mad_addr addr;
  size_t recv_size = sizeof(*mad);
  int status;

  if (mad_port == NULL || mad == NULL)
    return -1;

  pkey = oib_get_mgmt_pkey(mad_port, lid, 0);
  if (pkey == 0) {
    ERROR("Local port does not have management privileges\n");
    return -1;
  }

  BSWAP_MAD_HEADER((MAD *) mad);
  addr.lid = lid;
  addr.qpn = 1;
  addr.qkey = QP1_WELL_KNOWN_Q_KEY;
  addr.pkey = pkey;
  addr.sl = 0;
  status = oib_send_recv_mad_no_alloc(mad_port, (uint8_t *) mad,
                                      sizeof(STL_DATA_PORT_COUNTERS_REQ) + sizeof(MAD_COMMON),
                                      &addr, (uint8_t *) mad, &recv_size, RESP_WAIT_TIME, 0);
  BSWAP_MAD_HEADER((MAD *) mad);
  return (status == FSUCCESS) ? 0 : -1;
}

static void opa_publish_port_counters(struct stats *stats,
                                      STL_DATA_PORT_COUNTERS_RSP *rsp)
{
  if (stats == NULL || rsp == NULL)
    return;
#define X(n, r...) \
  do { \
    stats_set(stats, #n, rsp->Port[0].n); \
    TRACE(#n "%20" pr_iu64 "\n"); \
  } while (0)
  KEYS;
#undef X
}

static int collect_hfi_port(struct stats *stats, uint32_t port)
{
  struct oib_port *mad_port = NULL;
  STL_SMP smp;
  STL_PERF_MAD *mad = (STL_PERF_MAD *) &smp;
  STL_DATA_PORT_COUNTERS_REQ *req;
  STL_DATA_PORT_COUNTERS_RSP *rsp;
  IB_LID lid;
  int rc = -1;

  if (stats == NULL)
    return -1;

  opa_init_port_counters_mad(mad, port);
  req = (STL_DATA_PORT_COUNTERS_REQ *) &mad->PerfData;

  if (oib_open_port_by_num(&mad_port, (uint8) 0, port) != 0) {
    ERROR("cannot open MAD port %u\n", port);
    goto out;
  }

  if (oib_get_port_state(mad_port) != IB_PORT_ACTIVE) {
    fprintf(stderr, "WARNING port (%s:%d) is not ACTIVE!\n",
            oib_get_hfi_name(mad_port), oib_get_hfi_port_num(mad_port));
    ERROR("skipping inactive port %u", port);
    goto out;
  }

  lid = oib_get_port_lid(mad_port);
  if (opa_send_port_counters_mad(mad_port, mad, lid) != 0)
    goto out;

  rsp = (STL_DATA_PORT_COUNTERS_RSP *) req;
  BSWAP_STL_DATA_PORT_COUNTERS_RSP(rsp);
  opa_publish_port_counters(stats, rsp);
  rc = 0;

 out:
  if (mad_port != NULL)
    oib_close_port(mad_port);
  return rc;
}

struct opa_port_ctx {
  struct stats_type *type;
  const char *hfi;
};

static void opa_port_each(const char *base, const char *name, void *ctx)
{
  struct opa_port_ctx *pc = (struct opa_port_ctx *) ctx;
  int port;
  char dev[80];
  struct stats *stats;

  (void) base;
  if (pc == NULL || name == NULL)
    return;
  port = atoi(name);
  if (port <= 0)
    return;

  snprintf(dev, sizeof(dev), "%s/%d", pc->hfi, port);
  TRACE("IB HFI `%s', port %d, dev `%s'\n", pc->hfi, port, dev);
  stats = get_current_stats(pc->type, dev);
  if (stats == NULL)
    return;

  (void) collect_hfi_port(stats, (uint32_t) port);
}

static void opa_hfi_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *) ctx;
  char ports_path[160];
  struct opa_port_ctx pc = { type, name };

  if (type == NULL || name == NULL)
    return;
  snprintf(ports_path, sizeof(ports_path), "%s/%s/ports", base, name);
  sys_iter_for_each(ports_path, opa_port_each, &pc);
}

static void collect_opa(struct stats_type *type)
{
  if (type == NULL)
    return;
  sys_iter_for_each("/sys/class/infiniband", opa_hfi_each, type);
}

struct stats_type opa_stats_type = {
  .st_name = "host_opa",
  .st_collect = &collect_opa,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
