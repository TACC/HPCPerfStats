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
  X(DF_CTR3, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_SCALAR_DOUBLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_SCALAR_SINGLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE, "E,W=48", ""), \
  X(FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE, "E,W=48", ""), \
  X(ARM_EST_FLOPS, "E,W=64,U=FLOP", ""), \
  X(ARM_DRAM_BW_BYTES, "E,W=64,U=B", ""), \
  X(DCGM_CPU_POWER_UTIL_W, "U=W", "Grace DCGM per-socket CPU power (W); same value on each core in that socket"), \
  X(DCGM_CPU_POWER_LIMIT_W, "U=W", "Grace DCGM per-socket CPU power limit (W); same value on each core in that socket")

#endif
