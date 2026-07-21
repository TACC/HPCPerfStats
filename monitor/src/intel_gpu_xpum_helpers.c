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
    int i_dev_power = !rows[i].isTileData && intel_gpu_row_has_metric(&rows[i], XPUM_STATS_POWER);
    int best_dev_power =
        !rows[best].isTileData && intel_gpu_row_has_metric(&rows[best], XPUM_STATS_POWER);

    /* Prefer device-level row that already carries POWER (package reading). */
    if (i_dev_power && !best_dev_power) {
      best = i;
      continue;
    }
    if (!i_dev_power && best_dev_power)
      continue;
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

double intel_gpu_package_power_watts(const xpum_device_realtime_metrics_t *rows, uint32_t count)
{
  uint32_t i;
  double device_power = -1.0;
  double tile_sum = 0.0;
  int n_tile_power = 0;

  if (rows == NULL || count == 0)
    return -1.0;

  for (i = 0; i < count; i++) {
    const xpum_device_realtime_metric_t *m = intel_gpu_find_rt(&rows[i], XPUM_STATS_POWER);
    double watts;

    if (m == NULL)
      continue;
    watts = intel_gpu_scaled_u64(m->value, m->scale);
    if (!rows[i].isTileData) {
      if (watts > device_power)
        device_power = watts;
    } else {
      tile_sum += watts;
      n_tile_power++;
    }
  }

  if (device_power >= 0.0 && n_tile_power > 0) {
    /* Device may be average; xpumcli dump package ≈ sum of tiles on Max 1550. */
    return device_power >= tile_sum ? device_power : tile_sum;
  }
  if (device_power >= 0.0)
    return device_power;
  if (n_tile_power > 0)
    return tile_sum;
  return -1.0;
}

int intel_gpu_row_set_power_watts(xpum_device_realtime_metrics_t *row, double watts)
{
  int i;

  if (row == NULL || watts < 0.0)
    return -1;
  for (i = 0; i < row->count && i < XPUM_STATS_MAX; i++) {
    if (row->dataList[i].metricsType == XPUM_STATS_POWER) {
      row->dataList[i].isCounter = 0;
      row->dataList[i].value = (uint64_t)(watts + 0.5);
      row->dataList[i].scale = 1;
      return 0;
    }
  }
  if (row->count >= XPUM_STATS_MAX)
    return -1;
  row->dataList[row->count].metricsType = XPUM_STATS_POWER;
  row->dataList[row->count].isCounter = 0;
  row->dataList[row->count].value = (uint64_t)(watts + 0.5);
  row->dataList[row->count].scale = 1;
  row->count++;
  return 0;
}
