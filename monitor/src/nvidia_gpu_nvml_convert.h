#ifndef NVIDIA_GPU_NVML_CONVERT_H_
#define NVIDIA_GPU_NVML_CONVERT_H_

/*
 * Pure NVML / GPM -> dcgm_data_t unit conversions for nvidia_gpu (round 2, Approach J2). The
 * NVML path fills the same row the DCGM path fills, so publish and the estimate code stay
 * unchanged. Factors verified against embedded DCGM under dcgmproftester load on GB200
 * (finding 21): GPM ratios are percent (DCGM ratio x 100), GPM PCIe / NvLink are MiB/s
 * (DCGM bytes/s / 1048576). IMMA uses the same x0.01 by convention (D17).
 */

#include <stdint.h>

#define NVIDIA_GPU_NVML_MIB 1048576.0

/* GPM percent (0..100) -> DCGM ratio (0..1); out unchanged and -1 when not finite / out of range
 * (beyond a 0.5 % rounding slack). */
int nvidia_gpu_nvml_pct_to_ratio(double pct, double *out);
/* GPM MiB/s -> DCGM PROF bytes/s (rounded); negative / non-finite -> 0. */
uint64_t nvidia_gpu_nvml_mib_per_s_to_bytes(double mib_per_s);
/* nvmlDeviceGetPowerUsage milliwatts -> watts. */
double nvidia_gpu_nvml_mw_to_w(unsigned int mw);
/* nvmlMemory_t bytes -> DCGM FB MiB (rounded down, like the driver's MiB view). */
int64_t nvidia_gpu_nvml_bytes_to_mib(unsigned long long bytes);

#endif
