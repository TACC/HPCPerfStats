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
  X(PortXmitData, "E", ""),			\
    X(PortRcvData, "E", ""),			\
    X(PortXmitPkts, "E", ""),			\
    X(PortRcvPkts, "E", ""),			\
    X(PortMulticastXmitPkts, "E", ""),		\
    X(PortMulticastRcvPkts, "E", ""),		\
    X(PortXmitWait, "E", ""),			\
    X(SwPortCongestion, "E", ""),		\
    X(PortRcvFECN, "E", ""),			\
    X(PortRcvBECN, "E", ""),			\
    X(PortXmitTimeCong, "E", ""),		\
    X(PortXmitWastedBW, "E", ""),		\
    X(PortXmitWaitData, "E", ""),		\
    X(PortRcvBubble, "E", ""),			\
    X(PortMarkFECN, "E", ""),			\
    X(PortErrorCounterSummary, "E", "")

uint64_t g_transactID = 0xffffffff12340000;
#define RESP_WAIT_TIME (1000)   // 1000 milliseconds for receive response

static int get_numports(uint64 portmask)
{
  int i;
  int nports = 0;
  for (i = 0; i < MAX_PM_PORTS; i++) {
    if ((portmask >> i) & (uint64)1)
      nports++;
  }
  return nports;
}


static int collect_hfi_port(struct stats *stats, uint32_t port)
{
  int status = 0;
  struct oib_port *mad_port = NULL;
  STL_SMP smp;
  STL_PERF_MAD *mad = (STL_PERF_MAD*)&smp;
  MemoryClear(mad, sizeof(*mad));

  STL_DATA_PORT_COUNTERS_REQ *pStlDataPortCountersReq = (STL_DATA_PORT_COUNTERS_REQ *)&(mad->PerfData);
  STL_DATA_PORT_COUNTERS_RSP *pStlDataPortCountersRsp;

  uint32 attrmod;
  uint64 portmask = (uint64)1 << port;
  IB_LID lid;
  int rc = -1;
  pStlDataPortCountersReq->PortSelectMask[3] = portmask;
  pStlDataPortCountersReq->VLSelectMask = 0x1;
  attrmod = get_numports(portmask) << 24;

  BSWAP_STL_DATA_PORT_COUNTERS_REQ(pStlDataPortCountersReq);

  mad->common.BaseVersion = STL_BASE_VERSION;
  mad->common.ClassVersion = STL_PM_CLASS_VERSION;
  mad->common.MgmtClass = MCLASS_PERF;
  mad->common.u.NS.Status.AsReg16 = 0;
  mad->common.mr.AsReg8 = 0;
  mad->common.mr.s.Method = MMTHD_GET;
  mad->common.AttributeID = STL_PM_ATTRIB_ID_DATA_PORT_COUNTERS;
  mad->common.TransactionID = (++g_transactID);
  mad->common.AttributeModifier = attrmod;

  status = oib_open_port_by_num(&mad_port, (uint8)0, port);
  if (status != 0) {
    ERROR("cannot open MAD port %d\n", port);
    goto out;
  }

  if (oib_get_port_state(mad_port) != IB_PORT_ACTIVE) {
    fprintf(stderr, "WARNING port (%s:%d) is not ACTIVE!\n",
            oib_get_hfi_name(mad_port),
            oib_get_hfi_port_num(mad_port));
    ERROR("skipping inactive port %d", port);
    goto out;
  }

  lid = oib_get_port_lid(mad_port);
  uint16_t pkey = oib_get_mgmt_pkey(mad_port, lid, 0);
  if (pkey==0) {
    ERROR("Local port does not have management privileges\n");
    goto out;
  }

  BSWAP_MAD_HEADER((MAD*)mad);
  {
    struct oib_mad_addr addr = {
    lid  : lid,
    qpn  : 1,
    qkey : QP1_WELL_KNOWN_Q_KEY,
    pkey : pkey,
    sl   : 0
    };
    size_t recv_size = sizeof(*mad);
    status = oib_send_recv_mad_no_alloc(mad_port, (uint8_t *)mad,
                                        sizeof(STL_DATA_PORT_COUNTERS_REQ)+sizeof(MAD_COMMON),
                                        &addr,
                                        (uint8_t *)mad,
					&recv_size, RESP_WAIT_TIME, 0);
  }
  BSWAP_MAD_HEADER((MAD*)mad);
  if (status != FSUCCESS)
    goto out;

  pStlDataPortCountersRsp = (STL_DATA_PORT_COUNTERS_RSP *)pStlDataPortCountersReq;
  BSWAP_STL_DATA_PORT_COUNTERS_RSP(pStlDataPortCountersRsp);
#define X(n, r...)						\
  ({								\
    stats_set(stats, #n, pStlDataPortCountersRsp->Port[0].n);	\
    TRACE(#n"%20"PRIu64"\n");					\
  })
  KEYS;
#undef X
  //if (pStlDataPortCountersRsp->Port)
  //MemoryDeallocate(pStlDataPortCountersRsp->Port);
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
  struct opa_port_ctx *pc = (struct opa_port_ctx *)ctx;
  int port = atoi(name);
  char dev[80];
  struct stats *stats;

  (void)base;
  if (port <= 0)
    return;

  snprintf(dev, sizeof(dev), "%s/%d", pc->hfi, port);
  TRACE("IB HFI `%s', port %d, dev `%s'\n", pc->hfi, port, dev);
  stats = get_current_stats(pc->type, dev);
  if (stats == NULL)
    return;

  collect_hfi_port(stats, port);
}

static void opa_hfi_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *)ctx;
  char ports_path[160];
  struct opa_port_ctx pc = { type, name };

  snprintf(ports_path, sizeof(ports_path), "%s/%s/ports", base, name);
  sys_iter_for_each(ports_path, opa_port_each, &pc);
}

static void collect_opa(struct stats_type *type)
{
  sys_iter_for_each("/sys/class/infiniband", opa_hfi_each, type);
}

struct stats_type opa_stats_type = {
  .st_name = "opa",
  .st_collect = &collect_opa,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
