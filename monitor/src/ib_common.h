#ifndef _IB_COMMON_H_
#define _IB_COMMON_H_

typedef void (*ib_hca_port_fn)(const char *hca, int port, void *ctx);

int ib_port_collectible(const char *hca, int port);
void ib_foreach_hca_port(ib_hca_port_fn fn, void *ctx);

#endif
