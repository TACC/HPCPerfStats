#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "collect.h"
#include "dcgm_agent.h"
#include "dcgm_structs.h"
#include "dcgm_fields.h"
#include "nvidia_gpu.h"
#include "stats.h"
#include "trace.h"

/* Current DCGM uses DCGM_FI_DEV_CLOCK_THROTTLE_REASONS; older headers used *_CLOCKS_EVENT_*. */
#ifndef DCGM_FI_DEV_CLOCK_THROTTLE_REASONS
# ifdef DCGM_FI_DEV_CLOCKS_EVENT_REASONS
#  define DCGM_FI_DEV_CLOCK_THROTTLE_REASONS DCGM_FI_DEV_CLOCKS_EVENT_REASONS
# endif
#endif

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
  DCGM_FI_DEV_CLOCK_THROTTLE_REASONS
};

static const char *dcgm_err(dcgmReturn_t rc)
{
  return errorString(rc);
}

/*
 * Newer DCGM host engines return the handle from dcgmStartEmbedded_v2 only; the legacy
 * dcgmStartEmbedded() pair can report DCGM_ST_OK while leaving *pDcgmHandle at 0.
 */
static dcgmReturn_t nvidia_gpu_start_embedded(dcgmHandle_t *outh)
{
  dcgmReturn_t rc;
  dcgmStartEmbeddedV2Params_v1 ep;

  memset(&ep, 0, sizeof(ep));
  ep.version = dcgmStartEmbeddedV2Params_version1;
  ep.opMode = DCGM_OPERATION_MODE_AUTO;
  ep.logFile = NULL;
  ep.severity = DcgmLoggingSeverityNone;

  rc = dcgmStartEmbedded_v2(&ep);
  if (rc == DCGM_ST_OK && ep.dcgmHandle != (dcgmHandle_t)0) {
    *outh = ep.dcgmHandle;
    return DCGM_ST_OK;
  }
  if (rc == DCGM_ST_OK && ep.dcgmHandle == (dcgmHandle_t)0)
    return dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, outh);
  if (rc == DCGM_ST_VER_MISMATCH || rc == DCGM_ST_NOT_SUPPORTED || rc == DCGM_ST_BADPARAM)
    return dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, outh);
  return rc;
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

  if (gpu_id >= DCGM_MAX_NUM_DEVICES)
    return -1;
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
      case DCGM_FI_DEV_CLOCK_THROTTLE_REASONS:
        data[gpu_id].clocks_event_reasons = values[i].value.i64;
        break;
      default:
        break;
    }
  }
  return 0;
}

/*
 * dcgmGetAllSupportedDevices / dcgmGetEntityGroupEntities use an IN/OUT count in practice.
 * Older host engines cap lists at 16 GPUs (see DCGM_MAX_NUM_DEVICES comment in dcgm_structs.h).
 * Some builds also reject DCGM_GEGE_FLAG_ONLY_SUPPORTED with DCGM_ST_BADPARAM.
 */
#define NVIDIA_DCGM_GPU_LIST_LEGACY_CAP 16

static dcgmReturn_t nvidia_gpu_discover_gpu_ids(dcgmHandle_t h,
                                                unsigned int *gpu_ids,
                                                int *pndev)
{
  static const int caps[] = { DCGM_MAX_NUM_DEVICES, NVIDIA_DCGM_GPU_LIST_LEGACY_CAP };
  size_t ci;
  dcgmReturn_t rc = DCGM_ST_BADPARAM;

  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetAllSupportedDevices(h, gpu_ids, pndev);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetEntityGroupEntities(h,
                                    DCGM_FE_GPU,
                                    (dcgm_field_eid_t *)gpu_ids,
                                    pndev,
                                    0);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetEntityGroupEntities(h,
                                    DCGM_FE_GPU,
                                    (dcgm_field_eid_t *)gpu_ids,
                                    pndev,
                                    DCGM_GEGE_FLAG_ONLY_SUPPORTED);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  for (ci = 0; ci < sizeof(caps) / sizeof(caps[0]); ci++) {
    *pndev = caps[ci];
    rc = dcgmGetAllDevices(h, gpu_ids, pndev);
    if (rc == DCGM_ST_OK)
      return rc;
    if (rc != DCGM_ST_BADPARAM && rc != DCGM_ST_NOT_SUPPORTED)
      return rc;
  }
  return rc;
}

static int nvidia_gpu_collect_dev(struct stats *stats, const dcgm_data_t *row)
{
  stats_set(stats, "temperature", I64_TO_LLU(row->temperature));
  stats_set(stats, "gpu_util", I64_TO_LLU(row->gpu_util));
  stats_set(stats, "mem_util", I64_TO_LLU(row->mem_util));
  stats_set(stats, "power_usage", DBL_TO_LLU(row->power_usage));
  stats_set(stats, "fp64_active", DBL_TO_LLU_PERCENT(row->fp64_active));
  stats_set(stats, "fp32_active", DBL_TO_LLU_PERCENT(row->fp32_active));
  stats_set(stats, "fp16_active", DBL_TO_LLU_PERCENT(row->fp16_active));
  stats_set(stats, "sm_active", DBL_TO_LLU_PERCENT(row->sm_active));
  stats_set(stats, "sm_occupancy", DBL_TO_LLU_PERCENT(row->sm_occupancy));
  stats_set(stats, "tensor_active", DBL_TO_LLU_PERCENT(row->tensor_active));
  stats_set(stats, "clocks_event_reasons", I64_TO_LLU(row->clocks_event_reasons));
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

  rc = nvidia_gpu_start_embedded(&dcgm_handle);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM embedded mode failed: %s\n", dcgm_err(rc));
    goto out;
  }
  if (dcgm_handle == (dcgmHandle_t)0) {
    ERROR("DCGM embedded mode returned null handle after v2 and legacy start\n");
    goto out;
  }

  rc = nvidia_gpu_discover_gpu_ids(dcgm_handle, gpu_ids, &ndev);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM list devices failed: %s\n", dcgm_err(rc));
    goto out;
  }
  if (ndev <= 0) {
    ERROR("DCGM reports no supported GPUs\n");
    goto out;
  }

  rc = dcgmGroupCreate(dcgm_handle, DCGM_GROUP_EMPTY, group_name, &group_id);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM group creation failed: %s\n", dcgm_err(rc));
    goto out;
  }
  for (i = 0; i < ndev; i++) {
    rc = dcgmGroupAddDevice(dcgm_handle, group_id, gpu_ids[i]);
    if (rc != DCGM_ST_OK) {
      ERROR("DCGM group add device gpu_id=%u failed: %s\n", gpu_ids[i], dcgm_err(rc));
      goto out;
    }
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

  /*
   * dcgmGetLatestValues passes each GPU's DCGM id (not 0..ndev-1) to list_field_values.
   * Size the scratch array by DCGM_MAX_NUM_DEVICES so callbacks never write past the end.
   */
  dcgm_data = (dcgm_data_t *) calloc((size_t) DCGM_MAX_NUM_DEVICES, sizeof(*dcgm_data));
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
    unsigned int gid = gpu_ids[i];

    snprintf(dev, sizeof(dev), "%d", i);
    stats = get_current_stats(type, dev);
    if (stats == NULL)
      continue;
    if (nvidia_gpu_collect_dev(stats, &dcgm_data[gid]) == 0)
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
