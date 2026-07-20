#ifndef HOST_CPU_H_
#define HOST_CPU_H_

#include "stats.h"

#define KEYS                                                                                       \
  X(user, "E,U=cs", "time in user mode"),                                                          \
      X(nice, "E,U=cs", "time in user mode with low priority"),                                    \
      X(system, "E,U=cs", "time in system mode"), X(idle, "E,U=cs", "time in idle task"),          \
      X(iowait, "E,U=cs", "time in I/O wait"), X(irq, "E,U=cs", "time in IRQ"),                    \
      X(softirq, "E,U=cs", "time in softIRQ")

#endif
