#ifndef ROOFLINE_HW_PEAK_IDENTITY_H_
#define ROOFLINE_HW_PEAK_IDENTITY_H_

#include <stddef.h>

#include "cpuid.h"

/* Published peaks (NVIDIA / TACC / Intel / AMD datasheets); allowlisted identity only —
 * no soft invent. */

/* Horizon / Vista GPU + Grace */
#define ROOFLINE_GB200_HBM_BW_BYTES_PER_S 8000000000000.0 /* 8 TB/s HBM3e per GPU */
#define ROOFLINE_GB200_SM_COUNT 148
#define ROOFLINE_GH200_SM_COUNT 132
#define ROOFLINE_GH200_HBM_BW_BYTES_PER_S 4000000000000.0   /* 4 TB/s HBM3 */
#define ROOFLINE_GH200_HBM3E_BW_BYTES_PER_S 4900000000000.0 /* 4.9 TB/s HBM3e */
#define ROOFLINE_GH200_HBM3E_MEM_MIB_MIN 140000.0           /* ~144 GiB class */
#define ROOFLINE_GH200_C2C_BW_BYTES_PER_S 900000000000.0    /* NVLink-C2C bidirectional */
#define ROOFLINE_GRACE_DRAM_BW_BYTES_PER_S 512000000000.0   /* 512 GB/s LPDDR5X */
#define ROOFLINE_GRACE_CPU_PART 0xd4f

/* Stampede3 / Lonestar6 GPU (smi name identity) */
#define ROOFLINE_H100_SM_COUNT 132
#define ROOFLINE_H100_HBM_BW_BYTES_PER_S 3350000000000.0 /* 3.35 TB/s HBM3 NVIDIA H100 SXM */
#define ROOFLINE_A100_40_SM_COUNT 108
#define ROOFLINE_A100_40_HBM_BW_BYTES_PER_S 1555000000000.0 /* 1.555 TB/s A100 PCIe 40GB */
/* RTX PRO 6000 Blackwell Server Edition: 24064 CUDA cores / 128 = 188 SMs; 1597 GB/s GDDR */
#define ROOFLINE_RTX_PRO_6000_SM_COUNT 188
#define ROOFLINE_RTX_PRO_6000_GDDR_BW_BYTES_PER_S 1597000000000.0

/* Node-level CPU DRAM/HBM when EDAC dimm_mem_speed sum is 0 (CPUID processor_t). */
#define ROOFLINE_SKYLAKE_X_DRAM_BW_BYTES_PER_S 256000000000.0 /* TACC S3 SKX DDR4-2666 6ch×2 */
#define ROOFLINE_CASCADE_LAKE_DRAM_BW_BYTES_PER_S                                                  \
  282000000000.0 /* DDR4-2933 6ch×2 (Frontera/Cornell CLX class) */
#define ROOFLINE_ICELAKE_SERVER_DRAM_BW_BYTES_PER_S 409600000000.0 /* TACC S3 ICX DDR4-3200 8ch×2 */
#define ROOFLINE_SAPPHIRE_RAPIDS_DDR_BW_BYTES_PER_S 614400000000.0 /* 8468-class DDR5-4800 8ch×2 */
#define ROOFLINE_SAPPHIRE_RAPIDS_MAX_HBM_BW_BYTES_PER_S                                            \
  3276800000000.0 /* Xeon Max 9480: 2×1638.4 GB/s HBM2e (McCalpin) */
#define ROOFLINE_AMD_MILAN_DRAM_BW_BYTES_PER_S 409600000000.0 /* LS6 2×7763 DDR4-3200 8ch */
#define ROOFLINE_AMD_GENOA_DRAM_BW_BYTES_PER_S 921600000000.0 /* 2×9454 DDR5-4800 12ch */
#define ROOFLINE_AMD_TURIN_DRAM_BW_BYTES_PER_S                                                     \
  1228000000000.0 /* 2×9555 614 GB/s/socket (AMD product brief) */

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
  ROOFLINE_CPU_PEAK_SOURCE_IDENTITY = 2 /* Grace CPU-part or x86/AMD CPUID DRAM/HBM */
};

int roofline_nvidia_sm_count_from_name(const char *name);
double roofline_nvidia_hbm_bw_from_name(const char *name);
/* HBM class for GH200 uses memory.total MiB (≥140000 → HBM3e); GB200 ignores mem. */
double roofline_nvidia_hbm_bw_from_name_mem(const char *name, double mem_total_mib);
double roofline_nvidia_c2c_bw_from_name(const char *name);
double roofline_grace_dram_bw_from_cpu_part(unsigned int cpu_part);

/*
 * Node-level DDR and/or HBM peak B/s from processor_t when EDAC speeds are missing.
 * model_name: /proc/cpuinfo "model name" (may be NULL); distinguishes Xeon Max (HBM)
 * from Sapphire Rapids DDR hosts that share SAPPHIRE_RAPIDS.
 * Returns 0 on success with at least one of *ddr_bw_out / *hbm_bw_out > 0; -1 otherwise.
 */
int roofline_cpu_mem_bw_from_processor(processor_t p, const char *model_name, double *ddr_bw_out,
                                       double *hbm_bw_out);

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
