/* host_ib_sw — IB traffic via switch-port extended counters (Ranger-era workaround). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
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
  X(rx_bytes, "E,U=4B", ""), \
  X(rx_packets, "E", ""), \
  X(tx_bytes, "E,U=4B", ""), \
  X(tx_packets, "E", "")

static unsigned long g_ib_sw_fail_streak;
static time_t g_ib_sw_skip_until;

static int ib_sw_skip_active(void)
{
  time_t now = time(NULL);

  if (g_ib_sw_skip_until <= 0 || now <= 0)
    return 0;
  if (now >= g_ib_sw_skip_until) {
    g_ib_sw_skip_until = 0;
    return 0;
  }
  return 1;
}

static int ib_sw_query_switch_counters(struct ibmad_port *mad_port, int mad_timeout,
                                       uint64_t *rx_bytes, uint64_t *rx_packets,
                                       uint64_t *tx_bytes, uint64_t *tx_packets)
{
  ib_portid_t sw_port_id = {
    .drpath = {
      .cnt = 1,
      .p = { 0, 1, },
    },
  };
  uint8_t sw_info[64];
  int sw_lid;
  int sw_port;
  uint8_t sw_pma[1024];

  if (mad_port == NULL || rx_bytes == NULL || rx_packets == NULL
      || tx_bytes == NULL || tx_packets == NULL)
    return -1;

  memset(sw_info, 0, sizeof(sw_info));
  if (smp_query_via(sw_info, &sw_port_id, IB_ATTR_PORT_INFO, 0, mad_timeout, mad_port) == NULL) {
    ERROR("cannot query port info: %m\n");
    return -1;
  }

  mad_decode_field(sw_info, IB_PORT_LID_F, &sw_lid);
  mad_decode_field(sw_info, IB_PORT_LOCAL_PORT_F, &sw_port);
  sw_port_id.lid = sw_lid;

  memset(sw_pma, 0, sizeof(sw_pma));
  if (pma_query_via(sw_pma, &sw_port_id, sw_port, mad_timeout, IB_GSI_PORT_COUNTERS_EXT,
                    mad_port) == NULL) {
    ERROR("cannot query performance counters of switch LID %d, port %d: %m\n", sw_lid, sw_port);
    return -1;
  }

  mad_decode_field(sw_pma, IB_PC_EXT_RCV_BYTES_F, rx_bytes);
  mad_decode_field(sw_pma, IB_PC_EXT_RCV_PKTS_F, rx_packets);
  mad_decode_field(sw_pma, IB_PC_EXT_XMT_BYTES_F, tx_bytes);
  mad_decode_field(sw_pma, IB_PC_EXT_XMT_PKTS_F, tx_packets);
  return 0;
}

static void ib_sw_publish_tx_rx_swap(struct stats *stats, uint64_t sw_rx_bytes,
                                     uint64_t sw_rx_packets, uint64_t sw_tx_bytes,
                                     uint64_t sw_tx_packets)
{
  if (stats == NULL)
    return;
  /* Switch port receives host transmits and vice versa. */
  stats_set(stats, "rx_bytes", sw_tx_bytes);
  stats_set(stats, "rx_packets", sw_tx_packets);
  stats_set(stats, "tx_bytes", sw_rx_bytes);
  stats_set(stats, "tx_packets", sw_rx_packets);
}

static void collect_hca_port(struct stats *stats, char *hca_name, int hca_port)
{
  struct ibmad_port *mad_port = NULL;
  int mad_timeout = 15;
  int mad_classes[] = { IB_SMI_DIRECT_CLASS, IB_PERFORMANCE_CLASS, };
  uint64_t sw_rx_bytes = 0;
  uint64_t sw_rx_packets = 0;
  uint64_t sw_tx_bytes = 0;
  uint64_t sw_tx_packets = 0;

  if (stats == NULL || hca_name == NULL)
    return;

  mad_port = mad_rpc_open_port(hca_name, hca_port, mad_classes, 2);
  if (mad_port == NULL) {
    g_ib_sw_fail_streak++;
    ERROR("cannot open MAD port for HCA `%s' port %d\n", hca_name, hca_port);
    goto out;
  }

  if (ib_sw_query_switch_counters(mad_port, mad_timeout, &sw_rx_bytes, &sw_rx_packets,
                                  &sw_tx_bytes, &sw_tx_packets) != 0) {
    g_ib_sw_fail_streak++;
    goto out;
  }
  g_ib_sw_fail_streak = 0;

  TRACE("sw_rx_bytes %lu, sw_rx_packets %lu, sw_tx_bytes %lu, sw_tx_packets %lu\n",
        sw_rx_bytes, sw_rx_packets, sw_tx_bytes, sw_tx_packets);

  ib_sw_publish_tx_rx_swap(stats, sw_rx_bytes, sw_rx_packets, sw_tx_bytes, sw_tx_packets);

 out:
  if (mad_port != NULL)
    mad_rpc_close_port(mad_port);
}

struct ib_sw_port_ctx {
  struct stats_type *type;
  const char *hca;
};

static int ib_sw_port_active(const char *hca, int port)
{
  char state_path[80];
  int state = -1;

  if (hca == NULL)
    return 0;
  snprintf(state_path, sizeof(state_path),
           "/sys/class/infiniband/%s/ports/%d/state", hca, port);
  if (pscanf(state_path, "%d", &state) != 1) {
    ERROR("cannot read state of IB HCA `%s' port %d: %m\n", hca, port);
    return 0;
  }
  return state == 4;
}

static void ib_sw_port_each(const char *base, const char *name, void *ctx)
{
  struct ib_sw_port_ctx *pc = (struct ib_sw_port_ctx *) ctx;
  int port;
  char dev[80];
  struct stats *stats;

  (void) base;
  if (pc == NULL || name == NULL)
    return;
  port = atoi(name);
  if (port <= 0)
    return;
  if (!ib_sw_port_active(pc->hca, port)) {
    TRACE("skipping inactive IB HCA `%s', port %d\n", pc->hca, port);
    return;
  }

  snprintf(dev, sizeof(dev), "%s/%d", pc->hca, port);
  TRACE("IB HCA `%s', port %d, dev `%s'\n", pc->hca, port, dev);

  stats = get_current_stats(pc->type, dev);
  if (stats == NULL)
    return;

  collect_hca_port(stats, (char *) pc->hca, port);
}

static void ib_sw_hca_each(const char *base, const char *name, void *ctx)
{
  struct stats_type *type = (struct stats_type *) ctx;
  char ports_path[160];
  struct ib_sw_port_ctx pc = { type, name };

  if (type == NULL || name == NULL)
    return;
  snprintf(ports_path, sizeof(ports_path), "%s/%s/ports", base, name);
  sys_iter_for_each(ports_path, ib_sw_port_each, &pc);
}

static void collect_ib_sw(struct stats_type *type)
{
  enum { fail_threshold = 8, cooldown_sec = 120 };

  if (type == NULL)
    return;
  if (ib_sw_skip_active())
    return;
  if (g_ib_sw_fail_streak >= fail_threshold) {
    g_ib_sw_skip_until = time(NULL) + cooldown_sec;
    monitor_log_warn("ib_sw: too many failures (%lu), skipping for %ds\n",
                     g_ib_sw_fail_streak, cooldown_sec);
    return;
  }
  sys_iter_for_each("/sys/class/infiniband", ib_sw_hca_each, type);
}

struct stats_type ib_sw_stats_type = {
  .st_name = "host_ib_sw",
  .st_collect = &collect_ib_sw,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
