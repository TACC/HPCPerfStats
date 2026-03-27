#ifndef _CPU_COUNTER_METRICS_H_
#define _CPU_COUNTER_METRICS_H_

#define CPU_COUNTER_METRICS_KEYS \
  X(CTL0, "C", ""), \
  X(CTL1, "C", ""), \
  X(CTL2, "C", ""), \
  X(CTL3, "C", ""), \
  X(CTL4, "C", ""), \
  X(CTL5, "C", ""), \
  X(CTL6, "C", ""), \
  X(CTL7, "C", ""), \
  X(CTR0, "E,W=48", ""), \
  X(CTR1, "E,W=48", ""), \
  X(CTR2, "E,W=48", ""), \
  X(CTR3, "E,W=48", ""), \
  X(CTR4, "E,W=48", ""), \
  X(CTR5, "E,W=48", ""), \
  X(CTR6, "E,W=48", ""), \
  X(CTR7, "E,W=48", ""), \
  X(FIXED_CTR0, "E,W=48", ""), \
  X(FIXED_CTR1, "E,W=48", ""), \
  X(FIXED_CTR2, "E,W=48", ""), \
  X(INST_RETIRED, "E,W=48", ""), \
  X(APERF, "E,W=48", ""), \
  X(MPERF, "E,W=48", ""), \
  X(DF_CTR0, "E,W=48", ""), \
  X(DF_CTR1, "E,W=48", ""), \
  X(DF_CTR2, "E,W=48", ""), \
  X(DF_CTR3, "E,W=48", "")

#endif
