#ifndef ROOFLINE_HW_PEAK_DETECT_H_
#define ROOFLINE_HW_PEAK_DETECT_H_

struct roofline_cached_peaks {
  int initialized;
  unsigned long long gpu_source;
  unsigned long long cpu_flops;
  unsigned long long cpu_bw;
  unsigned long long gpu_flops;
  unsigned long long gpu_mem_bw;
  unsigned long long gpu_io_bw;
};

void roofline_hw_peak_detect_fill_cache(struct roofline_cached_peaks *cache);

#endif
