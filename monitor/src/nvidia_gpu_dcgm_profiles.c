/* DCGM FieldGroupCreate field-id profiles and attempt-order helper for nvidia_gpu. */
#include <stddef.h>
#include "nvidia_gpu_dcgm_compat.h"
#include "nvidia_gpu_dcgm_profiles.h"

/*
 * Core DCGM watch list (works on stacks without PROF tensor IMMA/HMMA/DFMA field IDs).
 * Must stay aligned with the full list minus the three optional tensor split fields.
 */
static const unsigned short g_dcgm_field_ids_core[NVIDIA_GPU_DCGM_NCORE] = {
    DCGM_FI_DEV_POWER_USAGE,
    DCGM_FI_DEV_GPU_TEMP,
    DCGM_FI_DEV_MEM_COPY_UTIL,
    DCGM_FI_DEV_GPU_UTIL,
    DCGM_FI_DEV_FB_TOTAL,
    DCGM_FI_DEV_FB_USED,
    DCGM_FI_DEV_FB_FREE,
    DCGM_FI_DEV_SM_CLOCK,
    DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
    DCGM_FI_PROF_PIPE_FP64_ACTIVE,
    DCGM_FI_PROF_PIPE_FP32_ACTIVE,
    DCGM_FI_PROF_PIPE_FP16_ACTIVE,
    DCGM_FI_PROF_SM_ACTIVE,
    DCGM_FI_PROF_SM_OCCUPANCY,
    DCGM_FI_PROF_DRAM_ACTIVE,
    DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
    DCGM_FI_PROF_PCIE_TX_BYTES,
    DCGM_FI_PROF_PCIE_RX_BYTES,
    DCGM_FI_PROF_NVLINK_TX_BYTES,
    DCGM_FI_PROF_NVLINK_RX_BYTES,
    DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
    DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT};

/*
 * Non-PROF fallback for nodes where DCGM profiling watches are unsupported/permissioned off.
 * Keeps nvidia_gpu rows alive with basic telemetry and gpu_count.
 */
static const unsigned short g_dcgm_field_ids_basic[NVIDIA_GPU_DCGM_NBASIC] = {
    DCGM_FI_DEV_POWER_USAGE,
    DCGM_FI_DEV_GPU_TEMP,
    DCGM_FI_DEV_MEM_COPY_UTIL,
    DCGM_FI_DEV_GPU_UTIL,
    DCGM_FI_DEV_FB_TOTAL,
    DCGM_FI_DEV_FB_USED,
    DCGM_FI_DEV_FB_FREE,
    DCGM_FI_DEV_SM_CLOCK,
    DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
    DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
    DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT};

/*
 * Last-resort basic list for older DCGM (e.g. 3.1.8) that rejects soft-defined
 * board-power field IDs 1132/1133 at FieldGroupCreate. Preferred profiles keep
 * those IDs for Grace/Hopper stacks that accept them.
 */
static const unsigned short
    g_dcgm_field_ids_basic_no_board_power[NVIDIA_GPU_DCGM_NBASIC_NO_BOARD_POWER] = {
        DCGM_FI_DEV_POWER_USAGE,
        DCGM_FI_DEV_GPU_TEMP,
        DCGM_FI_DEV_MEM_COPY_UTIL,
        DCGM_FI_DEV_GPU_UTIL,
        DCGM_FI_DEV_FB_TOTAL,
        DCGM_FI_DEV_FB_USED,
        DCGM_FI_DEV_FB_FREE,
        DCGM_FI_DEV_SM_CLOCK,
        DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
        DCGM_FI_DEV_CLOCK_THROTTLE_REASONS};

static const unsigned short g_dcgm_field_ids[NVIDIA_GPU_NFIELDS] = {
    DCGM_FI_DEV_POWER_USAGE,
    DCGM_FI_DEV_GPU_TEMP,
    DCGM_FI_DEV_MEM_COPY_UTIL,
    DCGM_FI_DEV_GPU_UTIL,
    DCGM_FI_DEV_FB_TOTAL,
    DCGM_FI_DEV_FB_USED,
    DCGM_FI_DEV_FB_FREE,
    DCGM_FI_DEV_SM_CLOCK,
    DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
    DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE,
    DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE,
    DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE,
    DCGM_FI_PROF_PIPE_FP64_ACTIVE,
    DCGM_FI_PROF_PIPE_FP32_ACTIVE,
    DCGM_FI_PROF_PIPE_FP16_ACTIVE,
    DCGM_FI_PROF_SM_ACTIVE,
    DCGM_FI_PROF_SM_OCCUPANCY,
    DCGM_FI_PROF_DRAM_ACTIVE,
    DCGM_FI_DEV_CLOCK_THROTTLE_REASONS,
    DCGM_FI_PROF_PCIE_TX_BYTES,
    DCGM_FI_PROF_PCIE_RX_BYTES,
    DCGM_FI_PROF_NVLINK_TX_BYTES,
    DCGM_FI_PROF_NVLINK_RX_BYTES,
    DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT,
    DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT};

_Static_assert(sizeof(g_dcgm_field_ids) / sizeof(g_dcgm_field_ids[0]) == NVIDIA_GPU_NFIELDS,
               "g_dcgm_field_ids length must match NVIDIA_GPU_NFIELDS");
_Static_assert(sizeof(g_dcgm_field_ids_core) / sizeof(g_dcgm_field_ids_core[0]) ==
                   NVIDIA_GPU_DCGM_NCORE,
               "g_dcgm_field_ids_core length must match NVIDIA_GPU_DCGM_NCORE");
_Static_assert(sizeof(g_dcgm_field_ids_basic) / sizeof(g_dcgm_field_ids_basic[0]) ==
                   NVIDIA_GPU_DCGM_NBASIC,
               "g_dcgm_field_ids_basic length must match NVIDIA_GPU_DCGM_NBASIC");
_Static_assert(sizeof(g_dcgm_field_ids_basic_no_board_power) /
                       sizeof(g_dcgm_field_ids_basic_no_board_power[0]) ==
                   NVIDIA_GPU_DCGM_NBASIC_NO_BOARD_POWER,
               "g_dcgm_field_ids_basic_no_board_power length must match "
               "NVIDIA_GPU_DCGM_NBASIC_NO_BOARD_POWER");
_Static_assert(NVIDIA_GPU_DCGM_NBASIC_NO_BOARD_POWER == NVIDIA_GPU_DCGM_NBASIC - 2,
               "basic-no-board-power must be basic minus two board-power fields");

int nvidia_gpu_watch_attempt_order(int order[NVIDIA_GPU_WATCH_PROFILE_NR], int last_profile)
{
  int n = 0;
  int p;

  if (order == NULL)
    return 0;
  if (last_profile >= 0 && last_profile < NVIDIA_GPU_WATCH_PROFILE_NR)
    order[n++] = last_profile;
  for (p = 0; p < NVIDIA_GPU_WATCH_PROFILE_NR; p++) {
    int seen = 0;
    int i;
    for (i = 0; i < n; i++) {
      if (order[i] == p) {
        seen = 1;
        break;
      }
    }
    if (!seen)
      order[n++] = p;
  }
  return n;
}

int nvidia_gpu_watch_profile_select(int attempt, const unsigned short **fid_out,
                                    unsigned int *nf_out, const char **name_out)
{
  if (fid_out == NULL || nf_out == NULL || name_out == NULL)
    return -1;
  if (attempt == 0) {
    *nf_out = (unsigned int)NVIDIA_GPU_NFIELDS;
    *fid_out = g_dcgm_field_ids;
    *name_out = "full-prof";
    return 0;
  }
  if (attempt == 1) {
    *nf_out = (unsigned int)NVIDIA_GPU_DCGM_NCORE;
    *fid_out = g_dcgm_field_ids_core;
    *name_out = "core-prof";
    return 0;
  }
  if (attempt == 2) {
    *nf_out = (unsigned int)NVIDIA_GPU_DCGM_NBASIC;
    *fid_out = g_dcgm_field_ids_basic;
    *name_out = "basic-nonprof";
    return 0;
  }
  if (attempt == 3) {
    *nf_out = (unsigned int)NVIDIA_GPU_DCGM_NBASIC_NO_BOARD_POWER;
    *fid_out = g_dcgm_field_ids_basic_no_board_power;
    *name_out = "basic-no-board-power";
    return 0;
  }
  return -1;
}

int nvidia_gpu_watch_profile_has_field(int attempt, unsigned short field_id)
{
  const unsigned short *fid = NULL;
  unsigned int nf = 0;
  const char *name = NULL;
  unsigned int i;

  if (nvidia_gpu_watch_profile_select(attempt, &fid, &nf, &name) < 0 || fid == NULL)
    return 0;
  for (i = 0; i < nf; i++) {
    if (fid[i] == field_id)
      return 1;
  }
  return 0;
}
