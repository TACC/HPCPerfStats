/* Minimal oib_utils ABI for monitor OPA MAD dlopen compile (opaque + decls). */
#ifndef OIB_UTILS_H_
#define OIB_UTILS_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef uint64_t uint64;
typedef uint16_t IB_LID;

enum {
  IB_PORT_ACTIVE = 4,
  FSUCCESS = 0
};

struct oib_port;

struct oib_mad_addr {
  IB_LID lid;
  uint32_t qpn;
  uint32_t qkey;
  uint16_t pkey;
  uint8_t sl;
  uint8_t reserved[3];
};

int oib_open_port_by_num(struct oib_port **port, uint8 hfi, uint32 port_num);
void oib_close_port(struct oib_port *port);
int oib_get_port_state(struct oib_port *port);
IB_LID oib_get_port_lid(struct oib_port *port);
uint16_t oib_get_mgmt_pkey(struct oib_port *port, IB_LID lid, uint8_t hop);
int oib_send_recv_mad_no_alloc(struct oib_port *port, uint8_t *send_buf,
                               size_t send_size, struct oib_mad_addr *addr,
                               uint8_t *recv_buf, size_t *recv_size,
                               unsigned timeout_ms, unsigned flags);

#ifdef __cplusplus
}
#endif

#endif
