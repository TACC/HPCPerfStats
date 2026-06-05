#ifndef _IB_MAD_H_
#define _IB_MAD_H_

struct stats;

int ib_mad_ext_collect_cycle_ok(void);
void ib_mad_ext_collect_port(struct stats *stats, const char *hca, int port);

int ib_mad_sw_collect_cycle_ok(void);
void ib_mad_sw_collect_port(struct stats *stats, const char *hca, int port);

#endif
