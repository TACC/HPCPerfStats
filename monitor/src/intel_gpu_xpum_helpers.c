#include <stddef.h>

#include "intel_gpu_xpum_helpers.h"

double intel_gpu_scaled_u64(uint64_t value, uint32_t scale)
{
  if (scale == 0)
    return (double)value;
  return (double)value / (double)scale;
}

const xpum_device_realtime_metric_t *intel_gpu_find_rt(const xpum_device_realtime_metrics_t *row,
                                                       xpum_stats_type_t type)
{
  int i;

  if (row == NULL)
    return NULL;
  for (i = 0; i < row->count && i < XPUM_STATS_MAX; i++) {
    if (row->dataList[i].metricsType == type)
      return &row->dataList[i];
  }
  return NULL;
}

int intel_gpu_row_has_metric(const xpum_device_realtime_metrics_t *row, xpum_stats_type_t type)
{
  return intel_gpu_find_rt(row, type) != NULL;
}

int intel_gpu_row_is_usable(const xpum_device_realtime_metrics_t *row)
{
  if (row == NULL || row->count == 0)
    return 0;
  if (intel_gpu_row_has_metric(row, XPUM_STATS_POWER))
    return 1;
  if (intel_gpu_row_has_metric(row, XPUM_STATS_ENERGY))
    return 1;
  return 0;
}

uint32_t intel_gpu_pick_best_rt_row(const xpum_device_realtime_metrics_t *rows, uint32_t count)
{
  uint32_t best = 0;
  uint32_t i;

  if (count == 0)
    return 0;
  for (i = 0; i < count; i++) {
    if (rows[i].count > rows[best].count)
      best = i;
    else if (rows[i].count == rows[best].count && !rows[i].isTileData && rows[best].isTileData)
      best = i;
  }
  return best;
}

void intel_gpu_merge_tile_rows(const xpum_device_realtime_metrics_t *rows, uint32_t count,
                               xpum_device_realtime_metrics_t *out)
{
  uint32_t i;
  int j;

  if (out == NULL || rows == NULL || count == 0)
    return;
  *out = rows[0];
  for (i = 1; i < count; i++) {
    for (j = 0; j < rows[i].count && j < XPUM_STATS_MAX; j++) {
      xpum_stats_type_t type = rows[i].dataList[j].metricsType;

      if (intel_gpu_find_rt(out, type) != NULL)
        continue;
      if (out->count >= XPUM_STATS_MAX)
        break;
      out->dataList[out->count] = rows[i].dataList[j];
      out->count++;
    }
  }
}
