#ifndef NVIDIA_GPU_DCGM_COMPAT_H_
#define NVIDIA_GPU_DCGM_COMPAT_H_

#ifdef MONITOR_GPU_DCGM_DLOPEN
#include "dcgm_gpu_dyn.h"
#endif
#include "dcgm_gpu_api.h"

#ifndef DCGM_FI_DEV_CLOCK_THROTTLE_REASONS
#ifdef DCGM_FI_DEV_CLOCKS_EVENT_REASONS
#define DCGM_FI_DEV_CLOCK_THROTTLE_REASONS DCGM_FI_DEV_CLOCKS_EVENT_REASONS
#endif
#endif

#ifndef DCGM_FI_PROF_PCIE_TX_BYTES
#define DCGM_FI_PROF_PCIE_TX_BYTES 1009
#endif
#ifndef DCGM_FI_PROF_PCIE_RX_BYTES
#define DCGM_FI_PROF_PCIE_RX_BYTES 1010
#endif
#ifndef DCGM_FI_PROF_NVLINK_TX_BYTES
#define DCGM_FI_PROF_NVLINK_TX_BYTES 1011
#endif
#ifndef DCGM_FI_PROF_NVLINK_RX_BYTES
#define DCGM_FI_PROF_NVLINK_RX_BYTES 1012
#endif
#ifndef DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT
#define DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT 1132
#endif
#ifndef DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT
#define DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT 1133
#endif
#ifndef DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE
#define DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE 1013
#endif
#ifndef DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE
#define DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE 1014
#endif
#ifndef DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE
#define DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE 1015
#endif

#define DBL_TO_LLU(x) ((unsigned long long)((x) + 0.5))
#define DBL_TO_LLU_PERCENT(x) ((unsigned long long)((100.0 * (x)) + 0.5))
#define I64_TO_LLU(x) ((unsigned long long)(x))

static inline const char *nvidia_gpu_dcgm_err(dcgmReturn_t rc)
{
  return errorString(rc);
}

#endif
