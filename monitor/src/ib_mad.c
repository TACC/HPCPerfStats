/* host_ib - switch-port counter collection over MAD (sw_* keys). */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <time.h>
#include "ib_mad_api.h"
#include "stats.h"
#include "trace.h"
#include "monitor_log.h"
#include "monitor_release_log.h"
#include "ib_mad.h"

enum { IB_MAD_PORT_STATE_MAX = 32 };

/* Kept per HCA port so one failing port neither resets nor trips another port's backoff. */
struct ib_mad_port_state {
  char hca[64];
  int port;
  unsigned long fail_streak;
  time_t skip_until;
  int sw_absent;
};

static struct ib_mad_port_state g_ib_mad_ports[IB_MAD_PORT_STATE_MAX];
static size_t g_ib_mad_port_nr;

/* Ports beyond IB_MAD_PORT_STATE_MAX share the last slot. */
static struct ib_mad_port_state *ib_mad_port_state_get(const char *hca, int port)
{
  struct ib_mad_port_state *st;

  for (size_t i = 0; i < g_ib_mad_port_nr; i++)
    if (g_ib_mad_ports[i].port == port && strcmp(g_ib_mad_ports[i].hca, hca) == 0)
      return &g_ib_mad_ports[i];
  if (g_ib_mad_port_nr == IB_MAD_PORT_STATE_MAX)
    return &g_ib_mad_ports[IB_MAD_PORT_STATE_MAX - 1];
  st = &g_ib_mad_ports[g_ib_mad_port_nr++];
  snprintf(st->hca, sizeof(st->hca), "%s", hca);
  st->port = port;
  return st;
}

static int ib_mad_skip_active(time_t *skip_until)
{
  time_t now = time(NULL);

  if (*skip_until <= 0 || now <= 0)
    return 0;
  if (now >= *skip_until) {
    *skip_until = 0;
    return 0;
  }
  return 1;
}

/* Entering the cooldown leaves the streak one short of the threshold: after it one probe runs,
 * and a failed probe re-arms the cooldown. */
static int ib_mad_collect_cycle_ok(struct ib_mad_port_state *st, const char *hca, int port)
{
  enum { fail_threshold = 8, cooldown_sec = 120 };

  if (ib_mad_skip_active(&st->skip_until))
    return 0;
  if (st->fail_streak >= fail_threshold) {
    st->skip_until = time(NULL) + cooldown_sec;
    monitor_log_warn("host_ib mad_sw %s.%d: too many failures (%lu), skipping for %ds\n", hca, port,
                     st->fail_streak, cooldown_sec);
    st->fail_streak = fail_threshold - 1;
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

enum stats_sentinel ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port)
{
  struct ib_mad_port_state *st;
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
  enum stats_sentinel code = STATS_SENTINEL_BACKEND_ERROR;
#ifndef DEBUG
  int saved_stderr = -1;
  int null_fd = -1;
#endif

  if (stats == NULL || hca == NULL)
    return STATS_SENTINEL_BACKEND_ERROR;
  st = ib_mad_port_state_get(hca, port);
  if (st->sw_absent)
    return STATS_SENTINEL_NOT_PRESENT;
  if (!ib_mad_collect_cycle_ok(st, hca, port))
    return STATS_SENTINEL_BACKOFF;

#ifndef DEBUG
  monitor_stderr_quiet_begin(&saved_stderr, &null_fd);
#endif

  /* A refused SMI/PMA open does not recover (the port has no switch MAD access). */
  mad_port = mad_rpc_open_port((char *)hca, port, mad_classes, 2);
  if (mad_port == NULL) {
    st->sw_absent = 1;
    code = STATS_SENTINEL_NOT_PRESENT;
    ib_mad_report_fail("cannot open MAD port for HCA `%s' port %d; sw_* not present\n", hca, port);
    goto out;
  }

  if (STATS_FAULT("mad_query", hca, &errno) ||
      ib_mad_sw_query_switch_counters(mad_port, mad_timeout, &sw_rx_bytes, &sw_rx_packets,
                                      &sw_tx_bytes, &sw_tx_packets) != 0) {
    st->fail_streak++;
    goto out;
  }
  st->fail_streak = 0;
  ib_mad_note_success();

  TRACE("sw_rx_bytes %lu, sw_rx_packets %lu, sw_tx_bytes %lu, sw_tx_packets %lu\n", sw_rx_bytes,
        sw_rx_packets, sw_tx_bytes, sw_tx_packets);

  ib_mad_sw_publish_tx_rx_swap(stats, sw_rx_bytes, sw_rx_packets, sw_tx_bytes, sw_tx_packets);
  code = STATS_SENTINEL_OK;

out:
  if (mad_port != NULL)
    mad_rpc_close_port(mad_port);
#ifndef DEBUG
  monitor_stderr_quiet_end(&saved_stderr, &null_fd);
#endif
  return code;
}

void ib_mad_test_reset_backoff(void)
{
  memset(g_ib_mad_ports, 0, sizeof(g_ib_mad_ports));
  g_ib_mad_port_nr = 0;
}

void ib_mad_test_expire_backoff(void)
{
  for (size_t i = 0; i < g_ib_mad_port_nr; i++)
    g_ib_mad_ports[i].skip_until = 0;
}
