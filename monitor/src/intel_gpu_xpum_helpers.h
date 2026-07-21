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

/*
 * Package power for multi-tile PVC (Max 1550): prefer device-level POWER when present;
 * else sum tile POWERS. When both exist, take the larger (device may be average while
 * xpumcli dump reports package ≈ sum of tiles).
 * Returns < 0 when no POWER metric is present.
 */
double intel_gpu_package_power_watts(const xpum_device_realtime_metrics_t *rows, uint32_t count);

/* Overwrite or append XPUM_STATS_POWER on row (value=watts, scale=1). */
int intel_gpu_row_set_power_watts(xpum_device_realtime_metrics_t *row, double watts);

#ifdef __cplusplus
}
#endif

#endif
