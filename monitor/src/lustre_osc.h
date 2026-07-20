#ifndef LUSTRE_OSC_H_
#define LUSTRE_OSC_H_

#include "stats.h"

#define KEYS                                                                                       \
  X(read_bytes, "E,U=B", ""), X(write_bytes, "E,U=B", ""), X(ost_destroy, "E", ""),                \
      X(ost_punch, "E", ""), X(ost_read, "E", ""), X(ost_setattr, "E", ""),                        \
      X(ost_statfs, "E", ""), X(ost_write, "E", ""), X(reqs, "E", ""), X(wait, "E,U=us", "")

#endif
