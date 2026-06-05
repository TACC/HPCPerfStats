#ifndef _IB_MAD_H_
#define _IB_MAD_H_

#include <stdint.h>

struct stats;

void ib_mad_ext_decode_counters(struct stats *stats, uint8_t *mad_buf);

int ib_mad_ext_collect_cycle_ok(void);
void ib_mad_ext_collect_port(struct stats *stats, const char *hca, int port);

int ib_mad_sw_collect_cycle_ok(void);
void ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port);

#endif
