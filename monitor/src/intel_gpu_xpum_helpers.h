#ifndef INTEL_GPU_XPUM_HELPERS_H_
#define INTEL_GPU_XPUM_HELPERS_H_

#include "xpum_api.h"

#ifdef __cplusplus
extern "C" {
#endif

double intel_gpu_scaled_u64(uint64_t value, uint32_t scale);

const xpum_device_realtime_metric_t *intel_gpu_find_rt(const xpum_device_realtime_metrics_t *row,
                                                       xpum_stats_type_t type);

int intel_gpu_row_has_metric(const xpum_device_realtime_metrics_t *row, xpum_stats_type_t type);

int intel_gpu_row_is_usable(const xpum_device_realtime_metrics_t *row);

uint32_t intel_gpu_pick_best_rt_row(const xpum_device_realtime_metrics_t *rows, uint32_t count);

void intel_gpu_merge_tile_rows(const xpum_device_realtime_metrics_t *rows, uint32_t count,
                               xpum_device_realtime_metrics_t *out);

#ifdef __cplusplus
}
#endif

#endif
