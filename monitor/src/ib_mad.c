/* host_ib — MAD extended port and switch-port counter collection. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <time.h>
#include "ib_mad_api.h"
#include "stats.h"
#include "trace.h"
#include "pscanf.h"
#include "monitor_log.h"
#include "monitor_release_log.h"
#include "ib_mad.h"

#define IB_PC_EXT_F                                                                                \
  X(uint32_t, port_select, IB_PC_EXT_PORT_SELECT_F)                                                \
  X(uint32_t, counter_select, IB_PC_EXT_COUNTER_SELECT_F)                                          \
  X(uint64_t, port_xmit_data, IB_PC_EXT_XMT_BYTES_F)                                               \
  X(uint64_t, port_rcv_data, IB_PC_EXT_RCV_BYTES_F)                                                \
  X(uint64_t, port_xmit_pkts, IB_PC_EXT_XMT_PKTS_F)                                                \
  X(uint64_t, port_rcv_pkts, IB_PC_EXT_RCV_PKTS_F)                                                 \
  X(uint64_t, port_unicast_xmit_pkts, IB_PC_EXT_XMT_UPKTS_F)                                       \
  X(uint64_t, port_unicast_rcv_pkts, IB_PC_EXT_RCV_UPKTS_F)                                        \
  X(uint64_t, port_multicast_xmit_pkts, IB_PC_EXT_XMT_MPKTS_F)                                     \
  X(uint64_t, port_multicast_rcv_pkts, IB_PC_EXT_RCV_MPKTS_F)

static unsigned long g_ib_mad_ext_fail_streak;
static time_t g_ib_mad_ext_skip_until;
static unsigned long g_ib_mad_sw_fail_streak;
static time_t g_ib_mad_sw_skip_until;

static int ib_mad_skip_active(time_t *skip_until)
{
  time_t now = time(NULL);

  if (skip_until == NULL || *skip_until <= 0 || now <= 0)
    return 0;
  if (now >= *skip_until) {
    *skip_until = 0;
    return 0;
  }
  return 1;
}

static int ib_mad_collect_cycle_ok(unsigned long *fail_streak, time_t *skip_until,
                                   const char *label)
{
  enum { fail_threshold = 8, cooldown_sec = 120 };

  if (ib_mad_skip_active(skip_until))
    return 0;
  if (fail_streak != NULL && *fail_streak >= fail_threshold) {
    *skip_until = time(NULL) + cooldown_sec;
    monitor_log_warn("host_ib %s: too many failures (%lu), skipping for %ds\n", label, *fail_streak,
                     cooldown_sec);
    return 0;
  }
  return 1;
}

/*! Count a MAD failure; emit ERROR only on first failure in release (always in DEBUG). */
static void
#if defined(__GNUC__) || defined(__clang__)
    __attribute__((format(printf, 1, 2)))
#endif
    ib_mad_report_fail(const char *fmt, ...)
{
  va_list ap;
  char buf[256];

  if (!monitor_release_fail_note(MONITOR_REL_FAIL_IB_MAD, monitor_release_log_first_only()))
    return;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  ERROR("%s", buf);
}

static void ib_mad_note_success(void)
{
  monitor_release_fail_clear(MONITOR_REL_FAIL_IB_MAD);
}

int ib_mad_ext_collect_cycle_ok(void)
{
  return ib_mad_collect_cycle_ok(&g_ib_mad_ext_fail_streak, &g_ib_mad_ext_skip_until, "mad_ext");
}

int ib_mad_sw_collect_cycle_ok(void)
{
  return ib_mad_collect_cycle_ok(&g_ib_mad_sw_fail_streak, &g_ib_mad_sw_skip_until, "mad_sw");
}

void ib_mad_ext_decode_counters(struct stats *stats, uint8_t *mad_buf)
{
  if (stats == NULL || mad_buf == NULL)
    return;
#define X(t, m, f)                                                                                 \
  do {                                                                                             \
    t m;                                                                                           \
    mad_decode_field(mad_buf, f, &m);                                                              \
    stats_set(stats, #m, m);                                                                       \
  } while (0);
  IB_PC_EXT_F;
#undef X
}

static void ib_mad_ext_query_lid_port(struct stats *stats, char *hca, int lid, int port)
{
  struct ibmad_port *mad_port = NULL;
  int mgmt_class = IB_PERFORMANCE_CLASS;
  ib_portid_t portid = {.lid = lid};
  uint8_t mad_buf[1024];
  int timeout = 0;
  int saved_stderr = -1;
  int null_fd = -1;

  if (stats == NULL || hca == NULL)
    return;

#ifndef DEBUG
  monitor_stderr_quiet_begin(&saved_stderr, &null_fd);
#endif

  mad_port = mad_rpc_open_port(hca, port, &mgmt_class, 1);
  if (mad_port == NULL) {
    g_ib_mad_ext_fail_streak++;
    ib_mad_report_fail("cannot open mad rpc port: %m\n");
    goto out;
  }

  memset(mad_buf, 0, sizeof(mad_buf));
  if (pma_query_via(mad_buf, &portid, port, timeout, IB_GSI_PORT_COUNTERS_EXT, mad_port) == NULL) {
    g_ib_mad_ext_fail_streak++;
    ib_mad_report_fail("cannot query performance counters: %m\n");
    goto out;
  }
  g_ib_mad_ext_fail_streak = 0;
  ib_mad_note_success();
  ib_mad_ext_decode_counters(stats, mad_buf);

out:
  if (mad_port != NULL)
    mad_rpc_close_port(mad_port);
#ifndef DEBUG
  monitor_stderr_quiet_end(&saved_stderr, &null_fd);
#endif
}

void ib_mad_ext_collect_port(struct stats *stats, const char *hca, int port)
{
  char path[80];
  unsigned int lid = 0;

  if (stats == NULL || hca == NULL)
    return;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%i/lid", hca, port);
  if (pscanf(path, "%x", &lid) != 1) {
    g_ib_mad_ext_fail_streak++;
    ib_mad_report_fail("cannot read lid of IB HCA `%s' port %d: %m\n", hca, port);
    return;
  }

  TRACE("IB HCA %s, port %d, lid %x\n", hca, port, lid);
  ib_mad_ext_query_lid_port(stats, (char *)hca, (int)lid, port);
}

static int ib_mad_sw_query_switch_counters(struct ibmad_port *mad_port, int mad_timeout,
                                           uint64_t *rx_bytes, uint64_t *rx_packets,
                                           uint64_t *tx_bytes, uint64_t *tx_packets)
{
  ib_portid_t sw_port_id = {
      .drpath =
          {
              .cnt = 1,
              .p =
                  {
                      0,
                      1,
                  },
          },
  };
  uint8_t sw_info[64];
  int sw_lid;
  int sw_port;
  uint8_t sw_pma[1024];

  if (mad_port == NULL || rx_bytes == NULL || rx_packets == NULL || tx_bytes == NULL ||
      tx_packets == NULL)
    return -1;

  memset(sw_info, 0, sizeof(sw_info));
  if (smp_query_via(sw_info, &sw_port_id, IB_ATTR_PORT_INFO, 0, mad_timeout, mad_port) == NULL) {
    ib_mad_report_fail("cannot query port info: %m\n");
    return -1;
  }

  mad_decode_field(sw_info, IB_PORT_LID_F, &sw_lid);
  mad_decode_field(sw_info, IB_PORT_LOCAL_PORT_F, &sw_port);
  sw_port_id.lid = sw_lid;

  memset(sw_pma, 0, sizeof(sw_pma));
  if (pma_query_via(sw_pma, &sw_port_id, sw_port, mad_timeout, IB_GSI_PORT_COUNTERS_EXT,
                    mad_port) == NULL) {
    ib_mad_report_fail("cannot query performance counters of switch LID %d, port %d: %m\n", sw_lid,
                       sw_port);
    return -1;
  }

  mad_decode_field(sw_pma, IB_PC_EXT_RCV_BYTES_F, rx_bytes);
  mad_decode_field(sw_pma, IB_PC_EXT_RCV_PKTS_F, rx_packets);
  mad_decode_field(sw_pma, IB_PC_EXT_XMT_BYTES_F, tx_bytes);
  mad_decode_field(sw_pma, IB_PC_EXT_XMT_PKTS_F, tx_packets);
  return 0;
}

void ib_mad_sw_publish_tx_rx_swap(struct stats *stats, uint64_t sw_rx_bytes, uint64_t sw_rx_packets,
                                  uint64_t sw_tx_bytes, uint64_t sw_tx_packets)
{
  if (stats == NULL)
    return;
  /* Switch port receives host transmits and vice versa. */
  stats_set(stats, "sw_rx_bytes", sw_tx_bytes);
  stats_set(stats, "sw_rx_packets", sw_tx_packets);
  stats_set(stats, "sw_tx_bytes", sw_rx_bytes);
  stats_set(stats, "sw_tx_packets", sw_rx_packets);
}

void ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port)
{
  struct ibmad_port *mad_port = NULL;
  int mad_timeout = 15;
  int mad_classes[] = {
      IB_SMI_DIRECT_CLASS,
      IB_PERFORMANCE_CLASS,
  };
  uint64_t sw_rx_bytes = 0;
  uint64_t sw_rx_packets = 0;
  uint64_t sw_tx_bytes = 0;
  uint64_t sw_tx_packets = 0;
  int saved_stderr = -1;
  int null_fd = -1;

  if (stats == NULL || hca == NULL)
    return;

#ifndef DEBUG
  monitor_stderr_quiet_begin(&saved_stderr, &null_fd);
#endif

  mad_port = mad_rpc_open_port((char *)hca, port, mad_classes, 2);
  if (mad_port == NULL) {
    g_ib_mad_sw_fail_streak++;
    ib_mad_report_fail("cannot open MAD port for HCA `%s' port %d\n", hca, port);
    goto out;
  }

  if (ib_mad_sw_query_switch_counters(mad_port, mad_timeout, &sw_rx_bytes, &sw_rx_packets,
                                      &sw_tx_bytes, &sw_tx_packets) != 0) {
    g_ib_mad_sw_fail_streak++;
    goto out;
  }
  g_ib_mad_sw_fail_streak = 0;
  ib_mad_note_success();

  TRACE("sw_rx_bytes %lu, sw_rx_packets %lu, sw_tx_bytes %lu, sw_tx_packets %lu\n", sw_rx_bytes,
        sw_rx_packets, sw_tx_bytes, sw_tx_packets);

  ib_mad_sw_publish_tx_rx_swap(stats, sw_rx_bytes, sw_rx_packets, sw_tx_bytes, sw_tx_packets);

out:
  if (mad_port != NULL)
    mad_rpc_close_port(mad_port);
#ifndef DEBUG
  monitor_stderr_quiet_end(&saved_stderr, &null_fd);
#endif
}

void ib_mad_test_reset_backoff(void)
{
  g_ib_mad_ext_fail_streak = 0;
  g_ib_mad_ext_skip_until = 0;
  g_ib_mad_sw_fail_streak = 0;
  g_ib_mad_sw_skip_until = 0;
}

void ib_mad_test_set_ext_fail_streak(unsigned long n)
{
  g_ib_mad_ext_fail_streak = n;
}

void ib_mad_test_set_sw_fail_streak(unsigned long n)
{
  g_ib_mad_sw_fail_streak = n;
}
