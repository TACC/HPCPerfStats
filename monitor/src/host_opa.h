#ifndef _HOST_OPA_H_
#define _HOST_OPA_H_

/* host_opa KEYS — STL Performance MAD names (Cornelis CN5000 / Intel OPA HFI). */
#define KEYS                                                                                       \
  X(port_xmit_data, "E", ""), X(port_rcv_data, "E", ""), X(port_xmit_pkts, "E", ""),               \
      X(port_rcv_pkts, "E", ""), X(port_multicast_xmit_pkts, "E", ""),                             \
      X(port_multicast_rcv_pkts, "E", ""), X(port_xmit_wait, "E", ""),                             \
      X(sw_port_congestion, "E", ""), X(port_rcv_fecn, "E", ""), X(port_rcv_becn, "E", ""),        \
      X(port_xmit_time_cong, "E", ""), X(port_xmit_wasted_bw, "E", ""),                            \
      X(port_xmit_wait_data, "E", ""), X(port_rcv_bubble, "E", ""), X(port_mark_fecn, "E", ""),    \
      X(port_error_counter_summary, "E", "")

#endif
