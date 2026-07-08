/* dcgm_gpu_dyn — runtime dlopen of libdcgm for x86 NVIDIA GPU path only. */
#include "dcgm_gpu_dyn.h"

#include "dcgm_agent.h"

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DCGM_GPU_DYN_SYM_LIST \
  X(dcgmInit) \
  X(dcgmShutdown) \
  X(dcgmStartEmbedded) \
  X(dcgmStartEmbedded_v2) \
  X(dcgmStopEmbedded) \
  X(dcgmConnect_v2) \
  X(dcgmDisconnect) \
  X(dcgmGetAllDevices) \
  X(dcgmGetAllSupportedDevices) \
  X(dcgmGetEntityGroupEntities) \
  X(dcgmGroupCreate) \
  X(dcgmGroupDestroy) \
  X(dcgmGroupAddDevice) \
  X(dcgmFieldGroupCreate) \
  X(dcgmFieldGroupDestroy) \
  X(dcgmWatchFields) \
  X(dcgmUpdateAllFields) \
  X(dcgmGetLatestValues) \
  X(errorString)

#define X(name) static __typeof__(name) *p_##name;
DCGM_GPU_DYN_SYM_LIST
#undef X

static void *g_dcgm_handle;
static int g_dcgm_loaded;
static char g_dcgm_last_error[256];

struct dcgm_gpu_dyn_test_hooks g_test_hooks_storage;
static struct dcgm_gpu_dyn_test_hooks *g_test_hooks;
static int g_test_hooks_active;

static void dcgm_gpu_dyn_set_error(const char *msg)
{
  if (msg == NULL)
    msg = "unknown error";
  snprintf(g_dcgm_last_error, sizeof(g_dcgm_last_error), "%s", msg);
}

const char *dcgm_gpu_dyn_last_error(void)
{
  return g_dcgm_last_error[0] != '\0' ? g_dcgm_last_error : "dcgm_gpu_dyn: no error recorded";
}

static int dcgm_gpu_dyn_resolve_one(void *lib, const char *sym, void **out)
{
  void *fn;

  if (out == NULL)
    return -1;
  *out = NULL;
  fn = dlsym(lib, sym);
  if (fn == NULL) {
    dcgm_gpu_dyn_set_error(sym);
    return -1;
  }
  *out = fn;
  return 0;
}

static int dcgm_gpu_dyn_try_open(const char *path)
{
  void *h;

  if (path == NULL || path[0] == '\0')
    return -1;
  h = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
  if (h == NULL) {
    dcgm_gpu_dyn_set_error(dlerror());
    return -1;
  }
  g_dcgm_handle = h;
  return 0;
}

static int dcgm_gpu_dyn_bind_symbols(void)
{
  void *lib = g_dcgm_handle;

#define X(name) \
  if (dcgm_gpu_dyn_resolve_one(lib, #name, (void **) &p_##name) < 0) \
    return -1;
  DCGM_GPU_DYN_SYM_LIST
#undef X
  return 0;
}

void dcgm_gpu_dyn_test_set_hooks(const struct dcgm_gpu_dyn_test_hooks *hooks)
{
  memset(&g_test_hooks_storage, 0, sizeof(g_test_hooks_storage));
  g_test_hooks = NULL;
  g_test_hooks_active = 0;
  if (hooks != NULL) {
    g_test_hooks_storage = *hooks;
    g_test_hooks = &g_test_hooks_storage;
    g_test_hooks_active = 1;
  }
}

int dcgm_gpu_dyn_load(void)
{
  static const char *default_libs[] = {
    "libdcgm.so.4",
    "libdcgm.so.3",
    "libdcgm.so",
    NULL
  };
  const char *override;
  size_t i;

  if (g_dcgm_loaded)
    return 0;

  g_dcgm_last_error[0] = '\0';
  override = getenv("HPCPERFSTATS_DCGM_LIB");
  if (override != NULL && override[0] != '\0') {
    if (dcgm_gpu_dyn_try_open(override) < 0)
      return -1;
  } else {
    for (i = 0; default_libs[i] != NULL; i++) {
      g_dcgm_last_error[0] = '\0';
      if (dcgm_gpu_dyn_try_open(default_libs[i]) == 0)
        break;
    }
    if (g_dcgm_handle == NULL)
      return -1;
  }

  if (dcgm_gpu_dyn_bind_symbols() < 0) {
    dlclose(g_dcgm_handle);
    g_dcgm_handle = NULL;
    return -1;
  }

  g_dcgm_loaded = 1;
  return 0;
}

int dcgm_gpu_dyn_loaded(void)
{
  return g_dcgm_loaded;
}

void dcgm_gpu_dyn_unload(void)
{
  if (g_dcgm_handle != NULL) {
    dlclose(g_dcgm_handle);
    g_dcgm_handle = NULL;
  }
  g_dcgm_loaded = 0;
  g_test_hooks_active = 0;
  memset(&g_test_hooks, 0, sizeof(g_test_hooks));
#define X(name) p_##name = NULL;
  DCGM_GPU_DYN_SYM_LIST
#undef X
}

#define DCGM_GPU_DYN_CALL(name, ...) \
  (g_test_hooks_active && g_test_hooks != NULL && g_test_hooks->name != NULL \
       ? g_test_hooks->name(__VA_ARGS__) \
       : (p_##name != NULL ? p_##name(__VA_ARGS__) : DCGM_ST_UNINITIALIZED))

dcgmReturn_t dcgm_gpu_dyn_dcgmInit(void)
{
  if (!g_dcgm_loaded && !g_test_hooks_active)
    return DCGM_ST_UNINITIALIZED;
  return DCGM_GPU_DYN_CALL(dcgmInit);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmShutdown(void)
{
  return DCGM_GPU_DYN_CALL(dcgmShutdown);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmStartEmbedded(dcgmOperationMode_t opMode, dcgmHandle_t *pDcgmHandle)
{
  return DCGM_GPU_DYN_CALL(dcgmStartEmbedded, opMode, pDcgmHandle);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmStartEmbedded_v2(dcgmStartEmbeddedV2Params_v1 *params)
{
  return DCGM_GPU_DYN_CALL(dcgmStartEmbedded_v2, params);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmStopEmbedded(dcgmHandle_t pDcgmHandle)
{
  return DCGM_GPU_DYN_CALL(dcgmStopEmbedded, pDcgmHandle);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmConnect_v2(char *ipAddress, dcgmConnectV2Params_t *connectParams,
                                         dcgmHandle_t *pDcgmHandle)
{
  return DCGM_GPU_DYN_CALL(dcgmConnect_v2, ipAddress, connectParams, pDcgmHandle);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmDisconnect(dcgmHandle_t pDcgmHandle)
{
  return DCGM_GPU_DYN_CALL(dcgmDisconnect, pDcgmHandle);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGetAllDevices(dcgmHandle_t pDcgmHandle, unsigned int gpuIdList[],
                                            int *count)
{
  return DCGM_GPU_DYN_CALL(dcgmGetAllDevices, pDcgmHandle, gpuIdList, count);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGetAllSupportedDevices(dcgmHandle_t pDcgmHandle,
                                                     unsigned int gpuIdList[], int *count)
{
  return DCGM_GPU_DYN_CALL(dcgmGetAllSupportedDevices, pDcgmHandle, gpuIdList, count);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGetEntityGroupEntities(dcgmHandle_t dcgmHandle,
                                                     dcgm_field_entity_group_t entityGroup,
                                                     dcgm_field_eid_t entities[], int *numEntities,
                                                     unsigned int flags)
{
  return DCGM_GPU_DYN_CALL(dcgmGetEntityGroupEntities, dcgmHandle, entityGroup, entities,
                           numEntities, flags);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGroupCreate(dcgmHandle_t pDcgmHandle, dcgmGroupType_t type,
                                          char *groupName, dcgmGpuGrp_t *pDcgmGroupId)
{
  return DCGM_GPU_DYN_CALL(dcgmGroupCreate, pDcgmHandle, type, groupName, pDcgmGroupId);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGroupDestroy(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId)
{
  return DCGM_GPU_DYN_CALL(dcgmGroupDestroy, pDcgmHandle, groupId);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGroupAddDevice(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                             unsigned int gpuId)
{
  return DCGM_GPU_DYN_CALL(dcgmGroupAddDevice, pDcgmHandle, groupId, gpuId);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmFieldGroupCreate(dcgmHandle_t dcgmHandle, int numFieldIds,
                                               unsigned short *fieldIds, char *fieldGroupName,
                                               dcgmFieldGrp_t *dcgmFieldGroupId)
{
  return DCGM_GPU_DYN_CALL(dcgmFieldGroupCreate, dcgmHandle, numFieldIds, fieldIds, fieldGroupName,
                           dcgmFieldGroupId);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmFieldGroupDestroy(dcgmHandle_t dcgmHandle,
                                                dcgmFieldGrp_t dcgmFieldGroupId)
{
  return DCGM_GPU_DYN_CALL(dcgmFieldGroupDestroy, dcgmHandle, dcgmFieldGroupId);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmWatchFields(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                          dcgmFieldGrp_t fieldGroupId, long long updateFreq,
                                          double maxKeepAge, int maxKeepSamples)
{
  return DCGM_GPU_DYN_CALL(dcgmWatchFields, pDcgmHandle, groupId, fieldGroupId, updateFreq,
                           maxKeepAge, maxKeepSamples);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmUpdateAllFields(dcgmHandle_t pDcgmHandle, int waitForUpdate)
{
  return DCGM_GPU_DYN_CALL(dcgmUpdateAllFields, pDcgmHandle, waitForUpdate);
}

dcgmReturn_t dcgm_gpu_dyn_dcgmGetLatestValues(dcgmHandle_t pDcgmHandle, dcgmGpuGrp_t groupId,
                                              dcgmFieldGrp_t fieldGroupId,
                                              dcgmFieldValueEnumeration_f enumCB, void *userData)
{
  return DCGM_GPU_DYN_CALL(dcgmGetLatestValues, pDcgmHandle, groupId, fieldGroupId, enumCB,
                           userData);
}

const char *dcgm_gpu_dyn_errorString(dcgmReturn_t result)
{
  if (g_test_hooks_active && g_test_hooks != NULL && g_test_hooks->errorString != NULL)
    return g_test_hooks->errorString(result);
  if (p_errorString != NULL)
    return p_errorString(result);
  return "dcgm_gpu_dyn: errorString unavailable";
}
