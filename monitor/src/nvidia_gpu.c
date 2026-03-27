#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "collect.h"
#include "dcgm_agent.h"
#include "dcgm_structs.h"
#include "nvidia_gpu.h"
#include "stats.h"
#include "trace.h"

#define DBL_TO_LLU(x) ((unsigned long long) ((x) + 0.5))
#define DBL_TO_LLU_PERCENT(x) ((unsigned long long) ((100.0 * (x)) + 0.5))
#define I64_TO_LLU(x) ((unsigned long long) (x))

static const unsigned short g_dcgm_field_ids[NVIDIA_GPU_NFIELDS] = {
  DCGM_FI_DEV_POWER_USAGE,
  DCGM_FI_DEV_GPU_TEMP,
  DCGM_FI_DEV_MEM_COPY_UTIL,
  DCGM_FI_DEV_GPU_UTIL,
  DCGM_FI_PROF_PIPE_TENSOR_ACTIVE,
  DCGM_FI_PROF_PIPE_FP64_ACTIVE,
  DCGM_FI_PROF_PIPE_FP32_ACTIVE,
  DCGM_FI_PROF_PIPE_FP16_ACTIVE,
  DCGM_FI_PROF_SM_ACTIVE,
  DCGM_FI_PROF_SM_OCCUPANCY,
  DCGM_FI_DEV_CLOCKS_EVENT_REASONS
};

static const char *dcgm_err(dcgmReturn_t rc)
{
  return errorString(rc);
}

static int bounded_ratio(double v, double *out)
{
  if (v >= 0.0 && v <= 1.0) {
    *out = v;
    return 0;
  }
  return -1;
}

static int list_field_values(unsigned int gpu_id,
                             dcgmFieldValue_v1 *values,
                             int num_values,
                             void *userdata)
{
  int i;
  dcgm_data_t *data = (dcgm_data_t *) userdata;
  for (i = 0; i < num_values; i++) {
    switch (values[i].fieldId) {
      case DCGM_FI_DEV_GPU_TEMP:
        data[gpu_id].temperature = values[i].value.i64;
        break;
      case DCGM_FI_DEV_POWER_USAGE:
        data[gpu_id].power_usage = values[i].value.dbl;
        break;
      case DCGM_FI_DEV_GPU_UTIL:
        data[gpu_id].gpu_util = values[i].value.i64;
        break;
      case DCGM_FI_DEV_MEM_COPY_UTIL:
        data[gpu_id].mem_util = values[i].value.i64;
        break;
      case DCGM_FI_PROF_SM_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].sm_active);
        break;
      case DCGM_FI_PROF_SM_OCCUPANCY:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].sm_occupancy);
        break;
      case DCGM_FI_PROF_PIPE_FP64_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].fp64_active);
        break;
      case DCGM_FI_PROF_PIPE_FP32_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].fp32_active);
        break;
      case DCGM_FI_PROF_PIPE_FP16_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].fp16_active);
        break;
      case DCGM_FI_PROF_PIPE_TENSOR_ACTIVE:
        (void) bounded_ratio(values[i].value.dbl, &data[gpu_id].tensor_active);
        break;
      case DCGM_FI_DEV_CLOCKS_EVENT_REASONS:
        data[gpu_id].clocks_event_reasons = values[i].value.i64;
        break;
      default:
        break;
    }
  }
  return 0;
}

static int nvidia_gpu_collect_dev(struct stats *stats, int i, dcgm_data_t *dcgm_data)
{
  stats_set(stats, "temperature", I64_TO_LLU(dcgm_data[i].temperature));
  stats_set(stats, "gpu_util", I64_TO_LLU(dcgm_data[i].gpu_util));
  stats_set(stats, "mem_util", I64_TO_LLU(dcgm_data[i].mem_util));
  stats_set(stats, "power_usage", DBL_TO_LLU(dcgm_data[i].power_usage));
  stats_set(stats, "fp64_active", DBL_TO_LLU_PERCENT(dcgm_data[i].fp64_active));
  stats_set(stats, "fp32_active", DBL_TO_LLU_PERCENT(dcgm_data[i].fp32_active));
  stats_set(stats, "fp16_active", DBL_TO_LLU_PERCENT(dcgm_data[i].fp16_active));
  stats_set(stats, "sm_active", DBL_TO_LLU_PERCENT(dcgm_data[i].sm_active));
  stats_set(stats, "sm_occupancy", DBL_TO_LLU_PERCENT(dcgm_data[i].sm_occupancy));
  stats_set(stats, "tensor_active", DBL_TO_LLU_PERCENT(dcgm_data[i].tensor_active));
  stats_set(stats, "clocks_event_reasons", I64_TO_LLU(dcgm_data[i].clocks_event_reasons));
  return 0;
}

static void nvidia_gpu_collect(struct stats_type *type)
{
  int i;
  int nr = 0;
  int ndev = 0;
  dcgmReturn_t rc;
  dcgmHandle_t dcgm_handle = (dcgmHandle_t) NULL;
  dcgmGpuGrp_t group_id = (dcgmGpuGrp_t) NULL;
  dcgmFieldGrp_t field_group_id = (dcgmFieldGrp_t) NULL;
  unsigned int gpu_ids[DCGM_MAX_NUM_DEVICES];
  dcgm_data_t *dcgm_data = NULL;
  char group_name[] = "gpu_all";

  rc = dcgmInit();
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM init failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, &dcgm_handle);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM embedded mode failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = dcgmGetAllSupportedDevices(dcgm_handle, gpu_ids, &ndev);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM list devices failed: %s\n", dcgm_err(rc));
    goto out;
  }
  if (ndev <= 0) {
    ERROR("DCGM reports no supported GPUs\n");
    goto out;
  }

  rc = dcgmGroupCreate(dcgm_handle, DCGM_GROUP_DEFAULT, group_name, &group_id);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM group creation failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = dcgmFieldGroupCreate(dcgm_handle,
                            NVIDIA_GPU_NFIELDS,
                            (unsigned short *) g_dcgm_field_ids,
                            (char *) "hpcperfstats_fields",
                            &field_group_id);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM field group creation failed: %s\n", dcgm_err(rc));
    goto out;
  }

  rc = dcgmWatchFields(dcgm_handle, group_id, field_group_id, 10000000, 3600.0, 3600);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM watch fields failed: %s\n", dcgm_err(rc));
    goto out;
  }
  usleep(10000000);

  dcgm_data = (dcgm_data_t *) calloc((size_t) ndev, sizeof(*dcgm_data));
  if (dcgm_data == NULL) {
    ERROR("Failed to allocate DCGM data buffer\n");
    goto out;
  }

  rc = dcgmGetLatestValues(dcgm_handle, group_id, field_group_id, &list_field_values, dcgm_data);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM fetch latest values failed: %s\n", dcgm_err(rc));
    goto out;
  }

  for (i = 0; i < ndev; i++) {
    struct stats *stats;
    char dev[80];
    snprintf(dev, sizeof(dev), "%d", i);
    stats = get_current_stats(type, dev);
    if (stats == NULL)
      continue;
    if (nvidia_gpu_collect_dev(stats, i, dcgm_data) == 0)
      nr++;
  }

out:
  if (dcgm_data != NULL)
    free(dcgm_data);
  if (field_group_id != (dcgmFieldGrp_t) NULL)
    (void) dcgmFieldGroupDestroy(dcgm_handle, field_group_id);
  if (group_id != (dcgmGpuGrp_t) NULL)
    (void) dcgmGroupDestroy(dcgm_handle, group_id);
  if (dcgm_handle != (dcgmHandle_t) NULL)
    (void) dcgmStopEmbedded(dcgm_handle);
  (void) dcgmShutdown();
  if (nr == 0)
    type->st_enabled = 0;
}

//! Definition of stats entry for this type
struct stats_type nvidia_gpu_stats_type = {
  .st_name = "nvidia_gpu",
  .st_collect = &nvidia_gpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
