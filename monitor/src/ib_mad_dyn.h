#ifndef IB_MAD_DYN_H_
#define IB_MAD_DYN_H_

#include <stdint.h>

#include <infiniband/mad.h>

#ifdef __cplusplus
extern "C" {
#endif

int ib_mad_dyn_load(void);
int ib_mad_dyn_loaded(void);
void ib_mad_dyn_unload(void);
const char *ib_mad_dyn_last_error(void);

struct ib_mad_dyn_test_hooks {
  struct ibmad_port *(*mad_rpc_open_port)(char *dev_name, int dev_port, int *mgmt_classes,
                                          int num_classes);
  void (*mad_rpc_close_port)(struct ibmad_port *srcport);
  uint8_t *(*pma_query_via)(void *rcvbuf, ib_portid_t *dest, int port, unsigned timeout,
                            unsigned id, const struct ibmad_port *srcport);
  uint8_t *(*smp_query_via)(void *buf, ib_portid_t *id, unsigned attrid, unsigned mod,
                            unsigned timeout, const struct ibmad_port *srcport);
  void (*mad_decode_field)(uint8_t *buf, enum MAD_FIELDS field, void *val);
};

void ib_mad_dyn_test_set_hooks(const struct ib_mad_dyn_test_hooks *hooks);

struct ibmad_port *ib_mad_dyn_mad_rpc_open_port(char *dev_name, int dev_port, int *mgmt_classes,
                                                int num_classes);
void ib_mad_dyn_mad_rpc_close_port(struct ibmad_port *srcport);
uint8_t *ib_mad_dyn_pma_query_via(void *rcvbuf, ib_portid_t *dest, int port, unsigned timeout,
                                  unsigned id, const struct ibmad_port *srcport);
uint8_t *ib_mad_dyn_smp_query_via(void *buf, ib_portid_t *id, unsigned attrid, unsigned mod,
                                  unsigned timeout, const struct ibmad_port *srcport);
void ib_mad_dyn_mad_decode_field(uint8_t *buf, enum MAD_FIELDS field, void *val);

#ifdef __cplusplus
}
#endif

#endif
