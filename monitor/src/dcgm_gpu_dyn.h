#ifndef DCGM_GPU_DYN_H_
#define DCGM_GPU_DYN_H_

#include "dcgm_structs.h"
#include "dcgm_fields.h"

#ifdef __cplusplus
extern "C" {
#endif

int dcgm_gpu_dyn_load(void);
int dcgm_gpu_dyn_loaded(void);
void dcgm_gpu_dyn_unload(void);
const char *dcgm_gpu_dyn_last_error(void);

struct dcgm_gpu_dyn_test_hooks {
  dcgmReturn_t (*dcgmInit)(void);
  dcgmReturn_t (*dcgmShutdown)(void);
  dcgmReturn_t (*dcgmStartEmbedded)(dcgmOperationMode_t opMode, dcgmHandle_t *pDcgmHandle);
  dcgmReturn_t (*dcgmStartEmbedded_v2)(dcgmStartEmbeddedV2Params_v1 *params);
  dcgmReturn_t (*dcgmStopEmbedded)(dcgmHandle_t pDcgmHandle);
  dcgmReturn_t (*dcgmConnect_v2)(char *ipAddress, dcgmConnectV2Params_t *connectParams,
                                 dcgmHandle_t *pDcgmHandle);
  dcgmReturn_t (*dcgmDisconnect)(dcgmHandle_t pDcgmHandle);
  dcgmReturn_t (*dcgmGetAllDevices)(dcgmHandle_t pDcgmHandle, unsigned int gpuIdList[], int *count);
  dcgmReturn_t (*dcgmGetAllSupportedDevices)(dcgmHandle_t pDcgmHandle, unsigned int gpuIdList[],
                                             int *count);
  dcgmReturn_t (*dcgmGetEntityGroupEntities)(dcgmHandle_t dcgmHandle,
                                             dcgm_field_entity_group_t entityGroup,
                                             dcgm_field_eid_t entities[], int *numEntities,
                                             unsigned int flags);
  dcgmReturn_t (*dcgmGroupCreate)(dcgmHandle_t pDcgmHandle, dcgmGroupType_t type, char *groupName,
                                  dcgmGpuGrp_t *pDcgmGroupId);
  dcgmReturn_t (*dcgmGroupDestroy)(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId);
  dcgmReturn_t (*dcgmGroupAddDevice)(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                     unsigned int gpuId);
  dcgmReturn_t (*dcgmFieldGroupCreate)(dcgmHandle_t dcgmHandle, int numFieldIds,
                                       unsigned short *fieldIds, char *fieldGroupName,
                                       dcgmFieldGrp_t *dcgmFieldGroupId);
  dcgmReturn_t (*dcgmFieldGroupDestroy)(dcgmHandle_t dcgmHandle, dcgmFieldGrp_t dcgmFieldGroupId);
  dcgmReturn_t (*dcgmWatchFields)(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                  dcgmFieldGrp_t fieldGroupId, long long updateFreq,
                                  double maxKeepAge, int maxKeepSamples);
  dcgmReturn_t (*dcgmUpdateAllFields)(dcgmHandle_t pDcgmHandle, int waitForUpdate);
  dcgmReturn_t (*dcgmGetLatestValues)(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                      dcgmFieldGrp_t fieldGroupId,
                                      dcgmFieldValueEnumeration_f enumCB, void *userData);
  const char *(*errorString)(dcgmReturn_t result);
};

/* Test hook: replace resolved symbols (non-NULL entries only). */
void dcgm_gpu_dyn_test_set_hooks(const struct dcgm_gpu_dyn_test_hooks *hooks);

dcgmReturn_t dcgm_gpu_dyn_dcgmInit(void);
dcgmReturn_t dcgm_gpu_dyn_dcgmShutdown(void);
dcgmReturn_t dcgm_gpu_dyn_dcgmStartEmbedded(dcgmOperationMode_t opMode, dcgmHandle_t *pDcgmHandle);
dcgmReturn_t dcgm_gpu_dyn_dcgmStartEmbedded_v2(dcgmStartEmbeddedV2Params_v1 *params);
dcgmReturn_t dcgm_gpu_dyn_dcgmStopEmbedded(dcgmHandle_t pDcgmHandle);
dcgmReturn_t dcgm_gpu_dyn_dcgmConnect_v2(char *ipAddress, dcgmConnectV2Params_t *connectParams,
                                         dcgmHandle_t *pDcgmHandle);
dcgmReturn_t dcgm_gpu_dyn_dcgmDisconnect(dcgmHandle_t pDcgmHandle);
dcgmReturn_t dcgm_gpu_dyn_dcgmGetAllDevices(dcgmHandle_t pDcgmHandle, unsigned int gpuIdList[],
                                            int *count);
dcgmReturn_t dcgm_gpu_dyn_dcgmGetAllSupportedDevices(dcgmHandle_t pDcgmHandle,
                                                     unsigned int gpuIdList[], int *count);
dcgmReturn_t dcgm_gpu_dyn_dcgmGetEntityGroupEntities(dcgmHandle_t dcgmHandle,
                                                     dcgm_field_entity_group_t entityGroup,
                                                     dcgm_field_eid_t entities[], int *numEntities,
                                                     unsigned int flags);
dcgmReturn_t dcgm_gpu_dyn_dcgmGroupCreate(dcgmHandle_t pDcgmHandle, dcgmGroupType_t type,
                                          char *groupName, dcgmGpuGrp_t *pDcgmGroupId);
dcgmReturn_t dcgm_gpu_dyn_dcgmGroupDestroy(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId);
dcgmReturn_t dcgm_gpu_dyn_dcgmGroupAddDevice(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                             unsigned int gpuId);
dcgmReturn_t dcgm_gpu_dyn_dcgmFieldGroupCreate(dcgmHandle_t dcgmHandle, int numFieldIds,
                                               unsigned short *fieldIds, char *fieldGroupName,
                                               dcgmFieldGrp_t *dcgmFieldGroupId);
dcgmReturn_t dcgm_gpu_dyn_dcgmFieldGroupDestroy(dcgmHandle_t dcgmHandle,
                                                dcgmFieldGrp_t dcgmFieldGroupId);
dcgmReturn_t dcgm_gpu_dyn_dcgmWatchFields(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                          dcgmFieldGrp_t fieldGroupId, long long updateFreq,
                                          double maxKeepAge, int maxKeepSamples);
dcgmReturn_t dcgm_gpu_dyn_dcgmUpdateAllFields(dcgmHandle_t pDcgmHandle, int waitForUpdate);
dcgmReturn_t dcgm_gpu_dyn_dcgmGetLatestValues(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                              dcgmFieldGrp_t fieldGroupId,
                                              dcgmFieldValueEnumeration_f enumCB, void *userData);
const char *dcgm_gpu_dyn_errorString(dcgmReturn_t result);

#ifdef __cplusplus
}
#endif

#endif
