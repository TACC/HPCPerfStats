#ifndef ROOFLINE_HW_PEAK_IDENTITY_H_
#define ROOFLINE_HW_PEAK_IDENTITY_H_

#include <stddef.h>

/* Published peaks (NVIDIA datasheet); allowlisted identity only — no soft invent. */
#define ROOFLINE_GB200_HBM_BW_BYTES_PER_S 8000000000000.0 /* 8 TB/s HBM3e per GPU */
#define ROOFLINE_GB200_SM_COUNT 148
#define ROOFLINE_GRACE_DRAM_BW_BYTES_PER_S 512000000000.0 /* 512 GB/s LPDDR5X */
#define ROOFLINE_GRACE_CPU_PART 0xd4f

/* GPU peak source enum (emitted as gpu_peak_source). */
enum {
  ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN = 0,
  ROOFLINE_GPU_PEAK_SOURCE_PROBED = 1,      /* DRM sysfs */
  ROOFLINE_GPU_PEAK_SOURCE_VENDOR_NVML = 2, /* NVML FLOPS */
  ROOFLINE_GPU_PEAK_SOURCE_VENDOR_SMI = 3,  /* nvidia-smi FLOPS/BW */
  ROOFLINE_GPU_PEAK_SOURCE_IDENTITY = 4     /* allowlisted name/CPU-part table */
};

/* CPU peak source enum (emitted as cpu_peak_source). */
enum {
  ROOFLINE_CPU_PEAK_SOURCE_FAIL_OPEN = 0,
  ROOFLINE_CPU_PEAK_SOURCE_PROBED = 1,  /* EDAC / procfs */
  ROOFLINE_CPU_PEAK_SOURCE_IDENTITY = 2 /* Grace CPU-part table */
};

int roofline_nvidia_sm_count_from_name(const char *name);
double roofline_nvidia_hbm_bw_from_name(const char *name);
double roofline_grace_dram_bw_from_cpu_part(unsigned int cpu_part);
double roofline_pcie_gen_lane_bytes_per_s(int gen);

/* Parse "name, clocks.max.sm, compute_cap" CSV line (noheader,nounits). */
int roofline_parse_smi_flops_line(const char *line, char *name, size_t name_cap, double *sm_mhz,
                                  int *cc_major, int *cc_minor);

/* Parse "name, memory.total, clocks.max.memory, pcie.gen, pcie.width" CSV line. */
int roofline_parse_smi_mem_pcie_line(const char *line, char *name, size_t name_cap,
                                     double *mem_total_mib, double *mem_clock_mhz, int *pcie_gen,
                                     int *pcie_width);

double roofline_nvidia_fp64_ratio_from_cc(int major, int minor, const char *name);
int roofline_nvidia_cuda_cores_per_sm(int major, int minor, const char *name);
double roofline_nvidia_fp64_flops_from_sm(int sm_count, int cores_per_sm, double sm_mhz,
                                          double fp64_ratio);

#endif
