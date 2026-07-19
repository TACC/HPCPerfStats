#ifndef OPA_MAD_API_H_
#define OPA_MAD_API_H_

/*
 * Routes OPA oib_* symbols to runtime dlopen wrappers when MONITOR_OPA_MAD_DLOPEN,
 * otherwise system IFS headers / link-time liboib_utils.
 */
#if defined(MONITOR_OPA_MAD_DLOPEN)

#include "opa_mad_dyn.h"

#define oib_open_port_by_num opa_mad_dyn_oib_open_port_by_num
#define oib_close_port opa_mad_dyn_oib_close_port
#define oib_get_port_state opa_mad_dyn_oib_get_port_state
#define oib_get_port_lid opa_mad_dyn_oib_get_port_lid
#define oib_get_mgmt_pkey opa_mad_dyn_oib_get_mgmt_pkey
#define oib_send_recv_mad_no_alloc opa_mad_dyn_oib_send_recv_mad_no_alloc

#else

#include "oib_utils.h"

#endif

#endif
