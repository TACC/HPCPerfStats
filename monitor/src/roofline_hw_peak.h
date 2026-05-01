#ifndef _ROOFLINE_HW_PEAK_H_
#define _ROOFLINE_HW_PEAK_H_

#define KEYS \
  X(cpu_peak_fp64_flops_per_s, "U=FLOP/s", "Host-level CPU FP64 peak throughput (FLOP/s)"), \
  X(cpu_peak_dram_bw_bytes_per_s, "U=B/s", "Host-level CPU DRAM peak bandwidth (bytes/s)"), \
  X(gpu_peak_fp64_flops_per_s, "U=FLOP/s", "Host-level GPU FP64 peak throughput (FLOP/s)"), \
  X(gpu_peak_mem_bw_bytes_per_s, "U=B/s", "Host-level GPU memory peak bandwidth (bytes/s)"), \
  X(gpu_peak_io_link_bw_bytes_per_s, "U=B/s", "Host-level GPU IO-link peak bandwidth (bytes/s)"), \
  X(cpu_peak_source, "", "CPU peak source enum"), \
  X(gpu_peak_source, "", "GPU peak source enum"), \
  X(peak_calc_version, "", "Peak calculation schema version")

struct stats_type;
extern struct stats_type roofline_hw_peak_stats_type;

#endif
