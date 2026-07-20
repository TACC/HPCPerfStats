#ifndef OPA_MAD_DYN_H_
#define OPA_MAD_DYN_H_

#include <stddef.h>
#include <stdint.h>

#include "oib_utils.h"

#ifdef __cplusplus
extern "C" {
#endif

int opa_mad_dyn_load(void);
int opa_mad_dyn_loaded(void);
void opa_mad_dyn_unload(void);
const char *opa_mad_dyn_last_error(void);

struct opa_mad_dyn_test_hooks {
  int (*oib_open_port_by_num)(struct oib_port **port, uint8 hfi, uint32 port_num);
  void (*oib_close_port)(struct oib_port *port);
  int (*oib_get_port_state)(struct oib_port *port);
  IB_LID (*oib_get_port_lid)(struct oib_port *port);
  uint16_t (*oib_get_mgmt_pkey)(struct oib_port *port, IB_LID lid, uint8_t hop);
  int (*oib_send_recv_mad_no_alloc)(struct oib_port *port, uint8_t *send_buf, size_t send_size,
                                    struct oib_mad_addr *addr, uint8_t *recv_buf, size_t *recv_size,
                                    unsigned timeout_ms, unsigned flags);
};

void opa_mad_dyn_test_set_hooks(const struct opa_mad_dyn_test_hooks *hooks);

int opa_mad_dyn_oib_open_port_by_num(struct oib_port **port, uint8 hfi, uint32 port_num);
void opa_mad_dyn_oib_close_port(struct oib_port *port);
int opa_mad_dyn_oib_get_port_state(struct oib_port *port);
IB_LID opa_mad_dyn_oib_get_port_lid(struct oib_port *port);
uint16_t opa_mad_dyn_oib_get_mgmt_pkey(struct oib_port *port, IB_LID lid, uint8_t hop);
int opa_mad_dyn_oib_send_recv_mad_no_alloc(struct oib_port *port, uint8_t *send_buf,
                                           size_t send_size, struct oib_mad_addr *addr,
                                           uint8_t *recv_buf, size_t *recv_size,
                                           unsigned timeout_ms, unsigned flags);

#ifdef __cplusplus
}
#endif

#endif
