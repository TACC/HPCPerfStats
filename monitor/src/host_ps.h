#ifndef HOST_PS_H_
#define HOST_PS_H_

#include "stats.h"

#define KEYS \
  X(ctxt, "E", "context switches"), \
  X(processes, "E", "forks"), \
  X(load_1, "", "1 minute load average (* 100)"), \
  X(load_5, "", "5 minute load average (* 100)"), \
  X(load_15, "", "15 minute load average (* 100)"), \
  X(nr_running, "", ""), \
  X(nr_threads, "", "")

#endif
