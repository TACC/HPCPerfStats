#ifndef _IB_MAD_H_
#define _IB_MAD_H_

#include <stdint.h>

struct stats;

void ib_mad_ext_decode_counters(struct stats *stats, uint8_t *mad_buf);

int ib_mad_ext_collect_cycle_ok(void);
void ib_mad_ext_collect_port(struct stats *stats, const char *hca, int port);

int ib_mad_sw_collect_cycle_ok(void);
void ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port);

void ib_mad_sw_publish_tx_rx_swap(struct stats *stats, uint64_t sw_rx_bytes,
                                  uint64_t sw_rx_packets, uint64_t sw_tx_bytes,
                                  uint64_t sw_tx_packets);

/* Test helpers: reset or seed MAD backoff state (unit tests only). */
void ib_mad_test_reset_backoff(void);
void ib_mad_test_set_ext_fail_streak(unsigned long n);
void ib_mad_test_set_sw_fail_streak(unsigned long n);

#endif
