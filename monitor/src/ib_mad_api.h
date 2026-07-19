#ifndef IB_MAD_API_H_
#define IB_MAD_API_H_

/*
 * Routes IB MAD symbols to runtime dlopen wrappers when MONITOR_IB_MAD_DLOPEN,
 * otherwise system / link-time libibmad headers.
 */
#if defined(MONITOR_IB_MAD_DLOPEN)

#include "ib_mad_dyn.h"

#define mad_rpc_open_port ib_mad_dyn_mad_rpc_open_port
#define mad_rpc_close_port ib_mad_dyn_mad_rpc_close_port
#define pma_query_via ib_mad_dyn_pma_query_via
#define smp_query_via ib_mad_dyn_smp_query_via
#define mad_decode_field ib_mad_dyn_mad_decode_field

#else

#include <infiniband/umad.h>
#include <infiniband/mad.h>

#endif

#endif
