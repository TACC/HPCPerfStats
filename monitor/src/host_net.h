#ifndef HOST_NET_H_
#define HOST_NET_H_

#include "stats.h"

#define KEYS                                                                                       \
  X(collisions, "E", ""), X(multicast, "E", ""), X(rx_bytes, "E,U=B", ""),                         \
      X(rx_compressed, "E", ""), X(rx_crc_errors, "E", ""), X(rx_dropped, "E", ""),                \
      X(rx_errors, "E", ""), X(rx_fifo_errors, "E", ""), X(rx_frame_errors, "E", ""),              \
      X(rx_length_errors, "E", ""), X(rx_missed_errors, "E", ""), X(rx_over_errors, "E", ""),      \
      X(rx_packets, "E", ""), X(tx_aborted_errors, "E", ""), X(tx_bytes, "E,U=B", ""),             \
      X(tx_carrier_errors, "E", ""), X(tx_compressed, "E", ""), X(tx_dropped, "E", ""),            \
      X(tx_errors, "E", ""), X(tx_fifo_errors, "E", ""), X(tx_heartbeat_errors, "E", ""),          \
      X(tx_packets, "E", ""), X(tx_window_errors, "E", "")

#endif
