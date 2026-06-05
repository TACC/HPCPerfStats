#ifndef NVIDIA_GPU_DCGM_WATCH_H_
#define NVIDIA_GPU_DCGM_WATCH_H_

#include "dcgm_agent.h"
#include "dcgm_structs.h"

enum nvidia_gpu_fail_stage {
  NVIDIA_GPU_FAIL_NONE = 0,
  NVIDIA_GPU_FAIL_DCGM_INIT,
  NVIDIA_GPU_FAIL_ATTACH,
  NVIDIA_GPU_FAIL_DISCOVERY,
  NVIDIA_GPU_FAIL_GROUP_CREATE,
  NVIDIA_GPU_FAIL_GROUP_ADD_DEVICE,
  NVIDIA_GPU_FAIL_FIELD_GROUP_CREATE,
  NVIDIA_GPU_FAIL_WATCH_FIELDS,
  NVIDIA_GPU_FAIL_ALLOC,
  NVIDIA_GPU_FAIL_FETCH,
  NVIDIA_GPU_FAIL_STAGE_NR
};

extern unsigned long g_nvidia_gpu_fail_counts[NVIDIA_GPU_FAIL_STAGE_NR];
extern unsigned long g_nvidia_gpu_gid_oob_skips;
extern unsigned long g_nvidia_gpu_stats_alloc_skips;
extern int g_nvidia_gpu_warmup_done;
extern int g_nvidia_gpu_runtime_ndev;
extern dcgmHandle_t g_nvidia_gpu_runtime_handle;
extern dcgmGpuGrp_t g_nvidia_gpu_runtime_group;
extern dcgmFieldGrp_t g_nvidia_gpu_runtime_field_group;
extern unsigned int g_nvidia_gpu_runtime_gpu_ids[];

int nvidia_gpu_runtime_prepare(int *fail_stage);
void nvidia_gpu_runtime_cleanup(void);

#endif
