/* Minimal STL / IBA types for host_opa MAD compile without IFS devel. */
#ifndef IBA_STL_SHIM_H_
#define IBA_STL_SHIM_H_

#include <stdint.h>
#include <string.h>
#include <inttypes.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifndef MemoryClear
#define MemoryClear(p, n) memset((p), 0, (n))
#endif

#ifndef pr_iu64
#define pr_iu64 PRIu64
#endif

typedef uint64_t uint64;
typedef uint32_t uint32;
typedef uint16_t uint16;
typedef uint8_t uint8;

enum {
  MAX_PM_PORTS = 256,
  STL_BASE_VERSION = 0x80,
  STL_PM_CLASS_VERSION = 0x80,
  MCLASS_PERF = 0x04,
  MMTHD_GET = 0x01,
  STL_PM_ATTRIB_ID_DATA_PORT_COUNTERS = 0x0042,
  QP1_WELL_KNOWN_Q_KEY = 0x80010000
};

typedef struct {
  uint16_t AsReg16;
} MAD_STATUS_UNION;

typedef struct {
  MAD_STATUS_UNION Status;
} MAD_NS;

typedef struct {
  uint8_t AsReg8;
  struct {
    uint8_t Method;
  } s;
} MAD_METHOD_UNION;

typedef struct {
  uint8_t BaseVersion;
  uint8_t MgmtClass;
  uint8_t ClassVersion;
  MAD_METHOD_UNION mr;
  union {
    MAD_NS NS;
  } u;
  uint64_t TransactionID;
  uint16_t AttributeID;
  uint16_t reserved;
  uint32_t AttributeModifier;
} MAD_COMMON;

typedef struct {
  MAD_COMMON common;
} MAD;

typedef struct {
  MAD_COMMON common;
  uint8_t PerfData[512];
} STL_PERF_MAD;

typedef struct {
  uint8_t raw[1024];
} STL_SMP;

typedef struct {
  uint64_t PortSelectMask[4];
  uint32_t VLSelectMask;
  uint32_t reserved;
} STL_DATA_PORT_COUNTERS_REQ;

/* Field names must match host_opa.h KEYS (rsp->Port[0].n). */
typedef struct {
  uint8_t port_number;
  uint8_t reserved[3];
  uint64_t port_xmit_data;
  uint64_t port_rcv_data;
  uint64_t port_xmit_pkts;
  uint64_t port_rcv_pkts;
  uint64_t port_multicast_xmit_pkts;
  uint64_t port_multicast_rcv_pkts;
  uint64_t port_xmit_wait;
  uint64_t sw_port_congestion;
  uint64_t port_rcv_fecn;
  uint64_t port_rcv_becn;
  uint64_t port_xmit_time_cong;
  uint64_t port_xmit_wasted_bw;
  uint64_t port_xmit_wait_data;
  uint64_t port_rcv_bubble;
  uint64_t port_mark_fecn;
  uint64_t port_error_counter_summary;
} STL_PORT_DATA_COUNTERS;

typedef struct {
  uint64_t PortSelectMask[4];
  uint32_t VLSelectMask;
  uint32_t reserved;
  STL_PORT_DATA_COUNTERS Port[1];
} STL_DATA_PORT_COUNTERS_RSP;

/* Endian helpers: no-ops in shim; real IFS swaps wire fields. */
#ifndef BSWAP_STL_DATA_PORT_COUNTERS_REQ
#define BSWAP_STL_DATA_PORT_COUNTERS_REQ(r) ((void) (r))
#endif
#ifndef BSWAP_STL_DATA_PORT_COUNTERS_RSP
#define BSWAP_STL_DATA_PORT_COUNTERS_RSP(r) ((void) (r))
#endif
#ifndef BSWAP_MAD_HEADER
#define BSWAP_MAD_HEADER(m) ((void) (m))
#endif

#ifdef __cplusplus
}
#endif

#endif
