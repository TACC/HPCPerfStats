#ifndef _CPU_COUNTER_METRICS_H_
#define _CPU_COUNTER_METRICS_H_

#define CPU_COUNTER_METRICS_KEYS \
  X(cpu_util_total_accum_us, "E,W=64", ""), \
  X(cpu_util_user_accum_us, "E,W=64", ""), \
  X(cpu_util_sys_accum_us, "E,W=64", ""), \
  X(cpu_util_irq_accum_us, "E,W=64", ""), \
  X(cpu_util_nice_accum_us, "E,W=64", ""), \
  X(cpu_clock_est_cycles, "E,W=64", ""), \
  X(instr_retired_any, "E,W=48", ""), \
  X(cycles_unhalted_core, "E,W=48", ""), \
  X(cycles_unhalted_ref, "E,W=48", ""), \
  X(mem_load_uops_retired_l1_hit, "E,W=48", ""), \
  X(mem_load_uops_retired_l2_hit, "E,W=48", ""), \
  X(mem_load_uops_retired_llc_hit, "E,W=48", ""), \
  X(l1d_replacement, "E,W=48", ""), \
  X(retired_instructions, "E,W=48", ""), \
  X(retired_branch_instr, "E,W=48", ""), \
  X(retired_misp_branch_instr, "E,W=48", ""), \
  X(ls_dispatch, "E,W=48", ""), \
  X(instr_retired, "E,W=48", ""), \
  X(aperf, "E,W=48", ""), \
  X(mperf, "E,W=48", ""), \
  X(dram_chan0_bytes, "E,W=48", ""), \
  X(dram_chan1_bytes, "E,W=48", ""), \
  X(dram_chan2_bytes, "E,W=48", ""), \
  X(dram_chan3_bytes, "E,W=48", ""), \
  X(fp_arith_inst_retired_scalar_double, "E,W=48", ""), \
  X(fp_arith_inst_retired_128b_packed_double, "E,W=48", ""), \
  X(fp_arith_inst_retired_256b_packed_double, "E,W=48", ""), \
  X(fp_arith_inst_retired_512b_packed_double, "E,W=48", ""), \
  X(fp_arith_inst_retired_scalar_single, "E,W=48", ""), \
  X(fp_arith_inst_retired_128b_packed_single, "E,W=48", ""), \
  X(fp_arith_inst_retired_256b_packed_single, "E,W=48", ""), \
  X(fp_arith_inst_retired_512b_packed_single, "E,W=48", ""), \
  X(arm_est_flops, "E,W=64,U=FLOP", ""), \
  X(arm_dram_bw_bytes, "E,W=64,U=B", ""), \
  X(dcgm_cpu_power_util_w, "U=W", "Grace DCGM per-socket CPU power (W); same value on each core in that socket"), \
  X(dcgm_cpu_power_limit_w, "U=W", "Grace DCGM per-socket CPU power limit (W); same value on each core in that socket")

#endif
