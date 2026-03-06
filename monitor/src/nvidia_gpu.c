
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <sys/time.h>


#include "collect.h"
#include "dcgm_agent.h"
#include "dcgm_structs.h"
#include "nvidia_gpu.h"
#include "stats.h"
#include "string1.h"
#include "trace.h"

// Assume x is always non-negative
#define DBL_TO_LLU_PERCENT(x) ((unsigned long long)((100.0 * x)+0.5))
#define DBL_TO_LLU(x) ((unsigned long long)((x)+0.5))
#define LLI_TO_LLU(x) ((unsigned long long)(x))

DCMG_DATA* dcgm_data;

static int list_field_values(
  unsigned int gpu_id, 
  dcgmFieldValue_v1 *values, 
  int numValues, 
  void *userdata)
{
  DCMG_DATA *data = (DCMG_DATA *) userdata;

  for (int i = 0; i < numValues; i++)  {

    switch (values[i].fieldId)  {
      /* 150 */
      case DCGM_FI_DEV_GPU_TEMP: 
        data[gpu_id].temperature = values[i].value.i64;
        // printf("\nGPU temp: %d \n", values[i].value.i64);
        break;
      /* 155 */
      case DCGM_FI_DEV_POWER_USAGE:
        data[gpu_id].power_usage = values[i].value.dbl;
        // printf("\nGPU power: %f \n", values[i].value.dbl);
        break;

      /* 203 */
      case DCGM_FI_DEV_GPU_UTIL: 
        data[gpu_id].gpu_util = values[i].value.i64;
        // printf("\nGPU util: %d \n", values[i].value.i64);

        break;
      /* 204 */
      case DCGM_FI_DEV_MEM_COPY_UTIL:
        data[gpu_id].mem_util = values[i].value.i64;
        // printf("\nGPU mem util: %d \n", values[i].value.i64);

        break;
      /* 1002 */
      case DCGM_FI_PROF_SM_ACTIVE: 
      if (values[i].value.dbl <= 1.0){
        data[gpu_id].sm_active = values[i].value.dbl;
      }
      else{
        data[gpu_id].sm_active = 1.23;
      }
        // printf("\nGPU sm active: %f \n", values[i].value.dbl);
        break;


      /* 1003 */
      case DCGM_FI_PROF_SM_OCCUPANCY: 
      if (values[i].value.dbl <= 1.0){
        data[gpu_id].sm_occupancy = values[i].value.dbl;
      }
      else{
        data[gpu_id].sm_occupancy = 1.23;
      }
        // printf("\nGPU sm occupancy: %f \n", values[i].value.dbl);
        break;

      /* 1006 */
      case DCGM_FI_PROF_PIPE_FP64_ACTIVE: 
      if (values[i].value.dbl <= 1.0){
        data[gpu_id].fp64_active = values[i].value.dbl;
      }
      else{
        data[gpu_id].fp64_active = 1.23;
      }
      // printf("\nGPU fp64: %f \n", values[i].value.dbl);
        break;


      /* 1007 */
      case DCGM_FI_PROF_PIPE_FP32_ACTIVE: 
      if (values[i].value.dbl <= 1.0){
        data[gpu_id].fp32_active = values[i].value.dbl;
      }
      else{
        data[gpu_id].fp32_active = 1.23;
      }    
      // printf("\nGPU fp32: %f \n", values[i].value.dbl);
        break;


      /* 1008 */
      case DCGM_FI_PROF_PIPE_FP16_ACTIVE: 
      if (values[i].value.dbl <= 1.0){
        data[gpu_id].fp16_active = values[i].value.dbl;
      }
      else{
        data[gpu_id].fp16_active = 1.23;
      }
        // printf("\nGPU fp16: %f \n", values[i].value.dbl);
        break;


      /* 1004 */
      case DCGM_FI_PROF_PIPE_TENSOR_ACTIVE: 
      if (values[i].value.dbl <= 1.0){
        data[gpu_id].tensor_active = values[i].value.dbl;
      }
      else{
        data[gpu_id].tensor_active = 1.23;
      }
        // printf("\nGPU tensor: %f \n", values[i].value.dbl);
        break;

      /* 112 */
      /* https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksEventReasons.html */
      /*Event in /usr/include/dcgm_fields.h */
      case DCGM_FI_DEV_CLOCKS_EVENT_REASONS:
        data[gpu_id].clocks_event_reasons = values[i].value.i64;
      break;


    //   /* 1009 */
    //   case DCGM_FI_PROF_PCIE_TX_BYTES:
    //     data[gpu_id].pcie_tx_bytes = values[i].value.i64;
    //     break;
    //   /* 1010 */
    //   case DCGM_FI_PROF_PCIE_RX_BYTES:
    //     data[gpu_id].pcie_rx_bytes = values[i].value.i64;
    //     break;
      
    //   case DCGM_FI_PROF_C2C_RX_ALL_BYTES:
    //     data[gpu_id].c2c_rx_bytes = values[i].value.i64;
    //     break;
    //   /* 202 */
    //   case DCGM_FI_DEV_PCIE_REPLAY_COUNTER: 
    //     data[gpu_id].pcie_replay_counter = values[i].value.i64;
    //     break;
    // /* 250 */
    // case DCGM_FI_DEV_FB_TOTAL: 
    //   data[gpu_id].fb_total = values[i].value.i64;
    //   break;
    // /* 251 */
    // case DCGM_FI_DEV_FB_FREE: 
    //   data[gpu_id].fb_free = values[i].value.i64;
    //   break;
    // /* 252 */
    // case DCGM_FI_DEV_FB_USED: 
    //   data[gpu_id].fb_used = values[i].value.i64;
    //   break;
    }
  }
  return 0;
}

static int nvidia_gpu_collect_dev(struct stats *stats, int i)
{

  stats_set(stats, "temperature",  LLI_TO_LLU(dcgm_data[i].temperature));
  // printf("temp: %d\n", LLI_TO_LLU(dcgm_data[i].temperature));
  stats_set(stats, "gpu_util",     LLI_TO_LLU(dcgm_data[i].gpu_util));
  // printf("util: %d\n", LLI_TO_LLU(dcgm_data[i].gpu_util));
  stats_set(stats, "mem_util",     LLI_TO_LLU(dcgm_data[i].mem_util));
  // printf("mem: %d\n", LLI_TO_LLU(dcgm_data[i].mem_util));
  stats_set(stats, "power_usage",  DBL_TO_LLU(dcgm_data[i].power_usage));
  // printf("power: %d\n", DBL_TO_LLU(dcgm_data[i].power_usage));
  stats_set(stats, "fp64_active",  DBL_TO_LLU_PERCENT(dcgm_data[i].fp64_active));
  // printf("fp64: %d\n", DBL_TO_LLU_PERCENT(dcgm_data[i].fp64_active));
  stats_set(stats, "fp32_active",  DBL_TO_LLU_PERCENT(dcgm_data[i].fp32_active));
  // printf("fp32: %d\n", DBL_TO_LLU_PERCENT(dcgm_data[i].fp32_active));
  stats_set(stats, "fp16_active",  DBL_TO_LLU_PERCENT(dcgm_data[i].fp16_active));
  // printf("fp16: %d\n", DBL_TO_LLU_PERCENT(dcgm_data[i].fp16_active));
  stats_set(stats, "sm_active",    DBL_TO_LLU_PERCENT(dcgm_data[i].sm_active));
  // printf("am_active: %d\n", DBL_TO_LLU_PERCENT(dcgm_data[i].sm_active));
  stats_set(stats, "sm_occupancy", DBL_TO_LLU_PERCENT(dcgm_data[i].sm_occupancy));
  // printf("sm_occ: %d\n", DBL_TO_LLU_PERCENT(dcgm_data[i].sm_occupancy));
  stats_set(stats, "tensor_active",DBL_TO_LLU_PERCENT(dcgm_data[i].tensor_active));
  stats_set(stats, "clocks_event_reasons",     LLI_TO_LLU(dcgm_data[i].clocks_event_reasons));

  // stats_set(stats, "fb_total",     LLI_TO_LLU(dcgm_data[i].fb_total));
  // stats_set(stats, "fb_free",      LLI_TO_LLU(dcgm_data[i].fb_free));
  // stats_set(stats, "fb_used",      LLI_TO_LLU(dcgm_data[i].fb_used));
  // stats_set(stats, "pcie_replay_counter", LLI_TO_LLU(dcgm_data[i].pcie_replay_counter));
  // stats_set(stats, "pcie_tx_bytes", LLI_TO_LLU(dcgm_data[i].pcie_tx_bytes));
  // stats_set(stats, "pcie_rx_bytes", LLI_TO_LLU(dcgm_data[i].pcie_rx_bytes));
  // stats_set(stats, "c2c_rx_bytes", LLI_TO_LLU(dcgm_data[i].c2c_rx_bytes));

  return 0;
}

static void nvidia_gpu_collect(struct stats_type *type)
{
  int ndev;
  int nr = 0;
  char groupName[] = "gpu_all";
  unsigned int gpuIdList[DCGM_MAX_NUM_DEVICES];

  dcgmReturn_t rc;
  dcgmHandle_t dcgmHandle = (dcgmHandle_t)NULL;
  dcgmGpuGrp_t myGroupId = (dcgmGpuGrp_t)NULL;


  rc = dcgmInit();

  if (rc != DCGM_ST_OK)  {
      ERROR("Error initializing DCGM engine. Return: %s\n", errorString(rc));
      goto out;
  }

  rc = dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, &dcgmHandle);

  if (rc != DCGM_ST_OK)  {
      ERROR("Error starting embedded DCGM engine. Return: %s\n", errorString(rc));
      goto out;
  }

  rc = dcgmGetAllSupportedDevices(dcgmHandle, gpuIdList, &ndev);

  if (rc != DCGM_ST_OK)  {
      ERROR("Error fetching devices. Return: %s\n", errorString(rc));
      goto out;
  }

  if (ndev == 0)  {
      ERROR("No Supported GPUs.\n");
      rc = DCGM_ST_GPU_NOT_SUPPORTED;
      goto out;
  }

  rc = dcgmGroupCreate(dcgmHandle, DCGM_GROUP_DEFAULT, groupName, &myGroupId);

  if (rc != DCGM_ST_OK)  {
      ERROR("Error creating group. Return: %s\n", errorString(rc));
      goto out;
  }

  dcgmFieldGrp_t fieldGroupId;

  unsigned short fieldIds[NFIELDS] = {
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
    
    // DCGM_FI_DEV_FB_TOTAL,
    // DCGM_FI_DEV_FB_USED,
    // DCGM_FI_DEV_FB_FREE,
    // DCGM_FI_PROF_PCIE_TX_BYTES,
    // DCGM_FI_PROF_PCIE_RX_BYTES,
    // DCGM_FI_DEV_PCIE_REPLAY_COUNTER,
    // DCGM_FI_PROF_C2C_RX_ALL_BYTES
  };

  rc = dcgmFieldGroupCreate(dcgmHandle, NFIELDS, &fieldIds[0], (char *)"fields", &fieldGroupId);

  if (rc != DCGM_ST_OK) {
      printf("Error creating field group: %s\n", errorString(rc));
      goto out;
  }

  long long loopIntervalUsec     = 10000000; /* How long should we sleep after each loop iteration */
  rc = dcgmWatchFields(dcgmHandle, myGroupId, fieldGroupId, loopIntervalUsec, 3600.0, 3600);


  if (rc != DCGM_ST_OK) {
      ERROR("Error setting watches: %s\n", errorString(rc));
      goto out;
  }


    dcgm_data = (DCMG_DATA*) malloc(sizeof(*dcgm_data) * ndev);

    // dcgmUpdateAllFields(dcgmHandle, 1);

    usleep(loopIntervalUsec);

    rc = dcgmGetLatestValues(
      dcgmHandle, myGroupId, fieldGroupId, &list_field_values, dcgm_data);

    if (rc != DCGM_ST_OK) {
        ERROR("Error getValues information. Return: %s\n", errorString(rc));
        goto out;
    }
    for (int i = 0; i < ndev; i++)  {
      struct stats *stats = NULL;
      char dev[80];
      snprintf(dev, sizeof(dev), "%d", i);
      stats = get_current_stats(type, dev);

      if (stats == NULL)
        goto out;

      if (nvidia_gpu_collect_dev(stats, i) == 0)
        nr++;
    }
    free(dcgm_data);
    dcgmGroupDestroy(dcgmHandle, myGroupId);
    dcgmStopEmbedded(dcgmHandle);
    dcgmShutdown();

out:
  if (nr == 0)
    type->st_enabled = 0;

  dcgmGroupDestroy(dcgmHandle, myGroupId);
  dcgmStopEmbedded(dcgmHandle);
  dcgmShutdown();

}

//! Definition of stats entry for this type
struct stats_type nvidia_gpu_stats_type = {
  .st_name = "nvidia_gpu",
  .st_collect = &nvidia_gpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};