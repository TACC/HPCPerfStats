/* host_ib_ext — IB extended port counters via MAD (GSI performance class). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <ctype.h>
#include <stdint.h>
#include <time.h>
#include <infiniband/umad.h>
#include <infiniband/mad.h>
#include "stats.h"
#include "trace.h"
#include "path_open_fail_once.h"
#include "pscanf.h"
#include "sys_iter.h"
#include "monitor_log.h"

#define KEYS \
  X(port_select, "c", ""), \
  X(counter_select, "c", ""), \
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

static unsigned long g_ib_ext_fail_streak;
static time_t g_ib_ext_skip_until;

static int ib_ext_skip_active(void)
{
  time_t now = time(NULL);

  if (g_ib_ext_skip_until <= 0 || now <= 0)
    return 0;
  if (now >= g_ib_ext_skip_until) {
    g_ib_ext_skip_until = 0;
    return 0;
  }
  return 1;
}

static void ib_ext_decode_counters(struct stats *stats, uint8_t *mad_buf)
{
#define X(t, m, f) \
  do { \
    t m; \
    mad_decode_field(mad_buf, f, &m); \
    stats_set(stats, #m, m); \
  } while (0);
  IB_PC_EXT_F;
#undef X
}

static void ib_ext_query_lid_port(struct stats *stats, char *hca, int lid, int port)
{
  struct ibmad_port *mad_port = NULL;
  int mgmt_class = IB_PERFORMANCE_CLASS;
  ib_portid_t portid = { .lid = lid };
  uint8_t mad_buf[1024];
  int timeout = 0;

  if (stats == NULL || hca == NULL)
    return;

  mad_port = mad_rpc_open_port(hca, port, &mgmt_class, 1);
  if (mad_port == NULL) {
    g_ib_ext_fail_streak++;
    ERROR("cannot open mad rpc port: %m\n");
    goto out;
  }

  memset(mad_buf, 0, sizeof(mad_buf));
  if (pma_query_via(mad_buf, &portid, port, timeout, IB_GSI_PORT_COUNTERS_EXT, mad_port) == NULL) {
    g_ib_ext_fail_streak++;
    ERROR("cannot query performance counters: %m\n");
    goto out;
  }
  g_ib_ext_fail_streak = 0;
  ib_ext_decode_counters(stats, mad_buf);

 out:
  if (mad_port != NULL)
    mad_rpc_close_port(mad_port);
}

static int ib_ext_port_active(char *hca, int port)
{
  char path[80];
  int state = -1;

  if (hca == NULL)
    return 0;
  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/state", hca, port);
  if (pscanf(path, "%d", &state) != 1) {
    ERROR("cannot read state of IB HCA `%s' port %d: %m\n", hca, port);
    return 0;
  }
  return state == 4;
}

static void ib_ext_collect_hca_port(struct stats_type *type, char *hca, int port)
{
  struct stats *stats = NULL;
  char dev[80];
  char path[80];
  unsigned int lid = 0;

  if (type == NULL || hca == NULL)
    return;
  if (!ib_ext_port_active(hca, port)) {
    TRACE("skipping inactive IB HCA `%s', port %d\n", hca, port);
    return;
  }

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%i/lid", hca, port);
  if (pscanf(path, "%x", &lid) != 1) {
    ERROR("cannot read lid of IB HCA `%s' port %d: %m\n", hca, port);
    return;
  }

  TRACE("IB HCA %s, port %d, lid %x\n", hca, port, lid);

  snprintf(dev, sizeof(dev), "%s/%d", hca, port);
  stats = get_current_stats(type, dev);
  if (stats == NULL)
    return;

  ib_ext_query_lid_port(stats, hca, lid, port);
}

struct ib_ext_port_ctx {
  struct stats_type *type;
  const char *hca;
};

static void ib_ext_port_each(const char *base, const char *name, void *ctx)
{
  struct ib_ext_port_ctx *pc = (struct ib_ext_port_ctx *) ctx;

  (void) base;
  if (pc == NULL || name == NULL)
    return;
  if (!isdigit((unsigned char) name[0]))
    return;
  ib_ext_collect_hca_port(pc->type, (char *) pc->hca, atoi(name));
}

static void ib_ext_hca_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *) ctx;
  char ports_path[160];
  struct ib_ext_port_ctx pc = { type, name };

  if (type == NULL || name == NULL)
    return;
  snprintf(ports_path, sizeof(ports_path), "%s/%s/ports", base, name);
  sys_iter_for_each(ports_path, ib_ext_port_each, &pc);
}

static void collect_ib_ext(struct stats_type *type)
{
  enum { fail_threshold = 8, cooldown_sec = 120 };

  if (type == NULL)
    return;
  if (ib_ext_skip_active())
    return;
  if (g_ib_ext_fail_streak >= fail_threshold) {
    g_ib_ext_skip_until = time(NULL) + cooldown_sec;
    monitor_log_warn("ib_ext: too many failures (%lu), skipping for %ds\n",
                     g_ib_ext_fail_streak, cooldown_sec);
    return;
  }
  sys_iter_for_each("/sys/class/infiniband", ib_ext_hca_each, type);
}

struct stats_type ib_ext_stats_type = {
  .st_name = "host_ib_ext",
  .st_collect = &collect_ib_ext,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
