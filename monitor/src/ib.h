#ifndef _HOST_IB_H_
#define _HOST_IB_H_

#define KEYS \
  X(excessive_buffer_overrun_errors, "E,W=32", ""), \
  X(link_downed, "E,W=32", "failed link error recoveries"), \
  X(link_error_recovery, "E,W=32", "successful link error recoveries"), \
  X(local_link_integrity_errors, "E,W=32", ""), \
  X(port_rcv_constraint_errors, "E,W=32", "packets discarded due to constraint"), \
  X(port_rcv_data, "E,W=32,U=4B", "data received"), \
  X(port_rcv_errors, "E,W=32", "bad packets received"), \
  X(port_rcv_packets, "E,W=32", "packets received"), \
  X(port_rcv_remote_physical_errors, "E,W=32", "EBP packets received"), \
  X(port_rcv_switch_relay_errors, "E,W=32", ""), \
  X(port_xmit_constraint_errors, "E,W=32", "packets not transmitted due to constraint"), \
  X(port_xmit_data, "E,W=32,U=4B", "data transmitted"), \
  X(port_xmit_discards, "E,W=32", "packets discarded due to down or congested port"), \
  X(port_xmit_packets, "E,W=32", "packets transmitted"), \
  X(port_xmit_wait, "E,W=32,U=ms", "wait time for credits or arbitration"), \
  X(symbol_error, "E,W=32", "minor link errors"), \
  X(vl15_dropped, "E,W=32", "")

#endif
