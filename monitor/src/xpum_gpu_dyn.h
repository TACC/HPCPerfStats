#ifndef XPUM_GPU_DYN_H_
#define XPUM_GPU_DYN_H_

#include "xpum_api.h"

#ifdef __cplusplus
extern "C" {
#endif

int xpum_gpu_dyn_load(void);
int xpum_gpu_dyn_loaded(void);
void xpum_gpu_dyn_unload(void);
const char *xpum_gpu_dyn_last_error(void);

struct xpum_gpu_dyn_test_hooks {
  xpum_result_t (*xpumInit)(void);
  xpum_result_t (*xpumShutdown)(void);
  xpum_result_t (*xpumGetDeviceList)(xpum_device_basic_info deviceList[], int *count);
  xpum_result_t (*xpumGetDeviceProperties)(xpum_device_id_t deviceId,
                                           xpum_device_properties_t *pXpumProperties);
  xpum_result_t (*xpumGetRealtimeMetrics)(xpum_device_id_t deviceId,
                                          xpum_device_realtime_metrics_t dataList[],
                                          uint32_t *count);
  xpum_result_t (*xpumGetStats)(xpum_device_id_t deviceId, xpum_device_stats_t dataList[],
                                uint32_t *count, uint64_t *begin, uint64_t *end,
                                uint64_t sessionId);
  xpum_result_t (*xpumGetFabricThroughputStats)(xpum_device_id_t deviceId,
                                                xpum_device_fabric_throughput_stats_t dataList[],
                                                uint32_t *count, uint64_t *begin, uint64_t *end,
                                                uint64_t sessionId);
};

void xpum_gpu_dyn_test_set_hooks(const struct xpum_gpu_dyn_test_hooks *hooks);

xpum_result_t xpum_gpu_dyn_xpumInit(void);
xpum_result_t xpum_gpu_dyn_xpumShutdown(void);
xpum_result_t xpum_gpu_dyn_xpumGetDeviceList(xpum_device_basic_info deviceList[], int *count);
xpum_result_t xpum_gpu_dyn_xpumGetDeviceProperties(xpum_device_id_t deviceId,
                                                   xpum_device_properties_t *pXpumProperties);
xpum_result_t xpum_gpu_dyn_xpumGetRealtimeMetrics(xpum_device_id_t deviceId,
                                                  xpum_device_realtime_metrics_t dataList[],
                                                  uint32_t *count);
xpum_result_t xpum_gpu_dyn_xpumGetStats(xpum_device_id_t deviceId, xpum_device_stats_t dataList[],
                                        uint32_t *count, uint64_t *begin, uint64_t *end,
                                        uint64_t sessionId);
xpum_result_t xpum_gpu_dyn_xpumGetFabricThroughputStats(
    xpum_device_id_t deviceId, xpum_device_fabric_throughput_stats_t dataList[], uint32_t *count,
    uint64_t *begin, uint64_t *end, uint64_t sessionId);

#ifdef __cplusplus
}
#endif

#endif
