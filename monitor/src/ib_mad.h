#ifndef _IB_MAD_H_
#define _IB_MAD_H_

#include <stdint.h>

#include "stats_sentinel.h"

struct stats;

/* Collect one HCA port's switch-port counters (sw_*); STATS_SENTINEL_OK when they were
 * published, otherwise the code for the sw_* keys: BACKOFF while the port's fail streak cools
 * down, BACKEND_ERROR on a failed attempt, and NOT_PRESENT for the daemon lifetime once the port
 * refused the switch MAD open (probed once per port). */
enum stats_sentinel ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port);

void ib_mad_sw_publish_tx_rx_swap(struct stats *stats, uint64_t sw_rx_bytes, uint64_t sw_rx_packets,
                                  uint64_t sw_tx_bytes, uint64_t sw_tx_packets);

/* Test helpers (unit tests only): forget every port's MAD state, or end every cooldown now. */
void ib_mad_test_reset_backoff(void);
void ib_mad_test_expire_backoff(void);

#endif
