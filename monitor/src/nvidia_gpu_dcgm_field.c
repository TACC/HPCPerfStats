/* Blank-aware DCGM field converters for nvidia_gpu assign path. */
#include <limits.h>
#include <stddef.h>

#include "nvidia_gpu_dcgm_compat.h"
#include "nvidia_gpu_dcgm_field.h"

/* Match third_party/nvidia-dcgm/dcgm_structs.h DCGM_FP64_BLANK / DCGM_INT64_BLANK (IS_BLANK). */
#define NVIDIA_GPU_DCGM_FP64_BLANK 140737488355328.0
#define NVIDIA_GPU_DCGM_INT64_BLANK 0x7ffffffffffffff0LL

int dcgm_fp64_value_is_blank(double v)
{
  return (v >= NVIDIA_GPU_DCGM_FP64_BLANK) ? 1 : 0;
}

int dcgm_int64_value_is_blank(long long v)
{
  return (v >= NVIDIA_GPU_DCGM_INT64_BLANK) ? 1 : 0;
}

static unsigned long long clamp_double_to_ull(double v)
{
  if (v <= 0.0)
    return 0ULL;
  if (v >= (double)ULLONG_MAX)
    return ULLONG_MAX;
  return (unsigned long long)(v + 0.5);
}

int nvidia_gpu_dcgm_field_watts(const dcgmFieldValue_v1 *v, double *out)
{
  if (out == NULL)
    return -1;
  *out = 0.0;
  if (v == NULL)
    return -1;
  if (v->fieldType == DCGM_FT_DOUBLE) {
    if (dcgm_fp64_value_is_blank(v->value.dbl))
      return -1;
    *out = v->value.dbl;
    return 0;
  }
  if (v->fieldType == DCGM_FT_INT64 && !dcgm_int64_value_is_blank(v->value.i64)) {
    *out = (double)v->value.i64;
    return 0;
  }
  return -1;
}

int nvidia_gpu_dcgm_field_u64(const dcgmFieldValue_v1 *v, uint64_t *out)
{
  if (out == NULL)
    return -1;
  *out = 0;
  if (v == NULL)
    return -1;
  if (v->fieldType == DCGM_FT_DOUBLE) {
    if (dcgm_fp64_value_is_blank(v->value.dbl) || v->value.dbl < 0.0)
      return -1;
    *out = (uint64_t)clamp_double_to_ull(v->value.dbl);
    return 0;
  }
  if (v->fieldType == DCGM_FT_INT64 && !dcgm_int64_value_is_blank(v->value.i64) &&
      v->value.i64 >= 0) {
    *out = (uint64_t)v->value.i64;
    return 0;
  }
  return -1;
}

int nvidia_gpu_dcgm_field_apply_i64(int64_t v, int64_t *out)
{
  if (out == NULL)
    return -1;
  if (dcgm_int64_value_is_blank((long long)v))
    return -1;
  *out = v;
  return 0;
}

int nvidia_gpu_dcgm_field_ratio(double v, double *out)
{
  if (out == NULL)
    return -1;
  if (dcgm_fp64_value_is_blank(v))
    return -1;
  if (v >= 0.0 && v <= 1.0) {
    *out = v;
    return 0;
  }
  return -1;
}

static const struct {
  unsigned short id;
  const char *key;
} g_dcgm_field_keys[] = {
    {DCGM_FI_DEV_GPU_TEMP, "temperature"},
    {DCGM_FI_DEV_POWER_USAGE, "power_usage"},
    {DCGM_FI_DEV_GPU_UTIL, "gpu_util"},
    {DCGM_FI_DEV_MEM_COPY_UTIL, "gpu_mem_util"},
    {DCGM_FI_DEV_FB_TOTAL, "gpu_mem_total_mb"},
    {DCGM_FI_DEV_FB_USED, "gpu_mem_used_mb"},
    {DCGM_FI_DEV_FB_FREE, "gpu_mem_free_mb"},
    {DCGM_FI_DEV_SM_CLOCK, "gpu_sm_clock"},
    {DCGM_FI_DEV_PCIE_REPLAY_COUNTER, "gpu_pcie_replay_counter"},
    {DCGM_FI_DEV_CLOCK_THROTTLE_REASONS, "clocks_event_reasons"},
    {DCGM_FI_PROF_DRAM_ACTIVE, "gpu_dram_active"},
    {DCGM_FI_PROF_SM_ACTIVE, "sm_active"},
    {DCGM_FI_PROF_SM_OCCUPANCY, "sm_occupancy"},
    {DCGM_FI_PROF_PIPE_FP64_ACTIVE, "fp64_active"},
    {DCGM_FI_PROF_PIPE_FP32_ACTIVE, "fp32_active"},
    {DCGM_FI_PROF_PIPE_FP16_ACTIVE, "fp16_active"},
    {DCGM_FI_PROF_PIPE_TENSOR_ACTIVE, "tensor_active"},
    {DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE, "tensor_imma_active"},
    {DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE, "tensor_hmma_active"},
    {DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE, "tensor_dfma_active"},
    {DCGM_FI_PROF_PCIE_TX_BYTES, "gpu_pcie_tx_bytes"},
    {DCGM_FI_PROF_PCIE_RX_BYTES, "gpu_pcie_rx_bytes"},
    {DCGM_FI_PROF_NVLINK_TX_BYTES, "gpu_nvlink_tx_bytes"},
    {DCGM_FI_PROF_NVLINK_RX_BYTES, "gpu_nvlink_rx_bytes"},
};

const char *nvidia_gpu_dcgm_field_key(unsigned short field_id)
{
  for (size_t i = 0; i < sizeof(g_dcgm_field_keys) / sizeof(g_dcgm_field_keys[0]); i++)
    if (g_dcgm_field_keys[i].id == field_id)
      return g_dcgm_field_keys[i].key;
  return NULL;
}

enum stats_sentinel nvidia_gpu_dcgm_status_sentinel(int status)
{
  return status == DCGM_ST_NOT_SUPPORTED ? STATS_SENTINEL_NOT_PRESENT : STATS_SENTINEL_READ_FAILED;
}

void nvidia_gpu_row_mark(dcgm_data_t *row, const char *key, enum stats_sentinel code)
{
  if (row == NULL || key == NULL || row->nmiss >= NVIDIA_GPU_ROW_MISS_MAX)
    return;
  row->miss[row->nmiss].key = key;
  row->miss[row->nmiss].code = (unsigned char)code;
  row->nmiss++;
}

void nvidia_gpu_dcgm_field_apply(dcgm_data_t *row, const dcgmFieldValue_v1 *v)
{
  int rc = 0;

  if (row == NULL || v == NULL)
    return;
  if (v->status != DCGM_ST_OK) {
    nvidia_gpu_row_mark(row, nvidia_gpu_dcgm_field_key(v->fieldId),
                        nvidia_gpu_dcgm_status_sentinel(v->status));
    return;
  }
  switch (v->fieldId) {
  case DCGM_FI_DEV_GPU_TEMP:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->temperature);
    break;
  case DCGM_FI_DEV_POWER_USAGE:
    rc = nvidia_gpu_dcgm_field_watts(v, &row->power_usage);
    break;
  case DCGM_FI_DEV_GPU_UTIL:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->gpu_util);
    break;
  case DCGM_FI_DEV_MEM_COPY_UTIL:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->mem_util);
    break;
  case DCGM_FI_DEV_FB_TOTAL:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->fb_total_mb);
    break;
  case DCGM_FI_DEV_FB_USED:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->fb_used_mb);
    break;
  case DCGM_FI_DEV_FB_FREE:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->fb_free_mb);
    break;
  case DCGM_FI_DEV_SM_CLOCK:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->sm_clock);
    break;
  case DCGM_FI_DEV_PCIE_REPLAY_COUNTER:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->pcie_replay_counter);
    break;
  case DCGM_FI_PROF_DRAM_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->dram_active);
    break;
  case DCGM_FI_PROF_SM_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->sm_active);
    break;
  case DCGM_FI_PROF_SM_OCCUPANCY:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->sm_occupancy);
    break;
  case DCGM_FI_PROF_PIPE_FP64_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->fp64_active);
    break;
  case DCGM_FI_PROF_PIPE_FP32_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->fp32_active);
    break;
  case DCGM_FI_PROF_PIPE_FP16_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->fp16_active);
    break;
  case DCGM_FI_PROF_PIPE_TENSOR_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->tensor_active);
    break;
  case DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->tensor_imma_active);
    break;
  case DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->tensor_hmma_active);
    break;
  case DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE:
    rc = nvidia_gpu_dcgm_field_ratio(v->value.dbl, &row->tensor_dfma_active);
    break;
  case DCGM_FI_DEV_CLOCK_THROTTLE_REASONS:
    rc = nvidia_gpu_dcgm_field_apply_i64(v->value.i64, &row->clocks_event_reasons);
    break;
  case DCGM_FI_PROF_PCIE_TX_BYTES:
    rc = nvidia_gpu_dcgm_field_u64(v, &row->prof_pcie_tx_bytes);
    break;
  case DCGM_FI_PROF_PCIE_RX_BYTES:
    rc = nvidia_gpu_dcgm_field_u64(v, &row->prof_pcie_rx_bytes);
    break;
  case DCGM_FI_PROF_NVLINK_TX_BYTES:
    rc = nvidia_gpu_dcgm_field_u64(v, &row->prof_nvlink_tx_bytes);
    break;
  case DCGM_FI_PROF_NVLINK_RX_BYTES:
    rc = nvidia_gpu_dcgm_field_u64(v, &row->prof_nvlink_rx_bytes);
    break;
  default:
    break;
  }
  /* Status OK but a blank (or out-of-range) value: a real 0 must never stand in for it. */
  if (rc < 0)
    nvidia_gpu_row_mark(row, nvidia_gpu_dcgm_field_key(v->fieldId), STATS_SENTINEL_INVALID_VALUE);
}
