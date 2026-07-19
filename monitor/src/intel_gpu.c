/* intel_gpu — Intel Data Center GPU (PVC) counters via XPU Manager (libxpum dlopen). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "intel_gpu.h"
#include "stats.h"
#include "trace.h"
#include "xpum_gpu_dyn.h"

#define INTEL_GPU_MISS_STREAK_MAX 3

static int g_xpum_ready;
static int g_miss_streak;
static xpum_device_id_t g_device_ids[XPUM_MAX_NUM_DEVICES];
static int g_device_count;

static double intel_gpu_scaled_u64(uint64_t value, uint32_t scale)
{
  if (scale == 0)
    return (double) value;
  return (double) value / (double) scale;
}

static void intel_gpu_env_prepare(void)
{
  /* On-demand pulls only — avoid XPUM background sampler jitter on HPC nodes. */
  setenv("XPUM_DISABLE_PERIODIC_METRIC_MONITOR", "1", 0);
  /* Enable util/power/temp/mem + PCIe counters + fabric + throttle when supported. */
  setenv("XPUM_METRICS", "0,1,4,6-10,34,35,37,38", 0);
}

static int intel_gpu_lookup_mem_total_mb(xpum_device_id_t id, unsigned long long *out_mb)
{
  xpum_device_properties_t props;
  int i;

  if (out_mb == NULL)
    return -1;
  *out_mb = 0;
  memset(&props, 0, sizeof(props));
  if (xpum_gpu_dyn_xpumGetDeviceProperties(id, &props) != XPUM_OK)
    return -1;
  for (i = 0; i < props.propertyLen && i < XPUM_MAX_NUM_PROPERTIES; i++) {
    if (props.properties[i].name == XPUM_DEVICE_PROPERTY_MEMORY_PHYSICAL_SIZE_BYTE) {
      unsigned long long bytes = strtoull(props.properties[i].value, NULL, 10);

      *out_mb = bytes / (1024ULL * 1024ULL);
      return 0;
    }
  }
  return -1;
}

static const xpum_device_realtime_metric_t *
intel_gpu_find_rt(const xpum_device_realtime_metrics_t *row, xpum_stats_type_t type)
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

static void intel_gpu_publish_from_rt(struct stats *stats, const xpum_device_realtime_metrics_t *row,
                                      unsigned long long mem_total_mb, int gpu_count)
{
  const xpum_device_realtime_metric_t *m;
  double used_bytes = 0.0;
  double util = 0.0;

  if (stats == NULL || row == NULL)
    return;

  m = intel_gpu_find_rt(row, XPUM_STATS_GPU_UTILIZATION);
  if (m != NULL)
    stats_set(stats, "gpu_util", (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));

  m = intel_gpu_find_rt(row, XPUM_STATS_MEMORY_UTILIZATION);
  if (m != NULL) {
    util = intel_gpu_scaled_u64(m->value, m->scale);
    stats_set(stats, "gpu_mem_util", (unsigned long long) (util + 0.5));
  }

  m = intel_gpu_find_rt(row, XPUM_STATS_MEMORY_USED);
  if (m != NULL)
    used_bytes = intel_gpu_scaled_u64(m->value, m->scale);
  stats_set(stats, "gpu_mem_used_mb", (unsigned long long) (used_bytes / (1024.0 * 1024.0) + 0.5));
  if (mem_total_mb == 0 && util > 0.0 && used_bytes > 0.0)
    mem_total_mb = (unsigned long long) ((used_bytes / (util / 100.0)) / (1024.0 * 1024.0) + 0.5);
  stats_set(stats, "gpu_mem_total_mb", mem_total_mb);

  m = intel_gpu_find_rt(row, XPUM_STATS_POWER);
  if (m != NULL)
    stats_set(stats, "power_usage",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));

  m = intel_gpu_find_rt(row, XPUM_STATS_GPU_CORE_TEMPERATURE);
  if (m != NULL)
    stats_set(stats, "temperature",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));

  m = intel_gpu_find_rt(row, XPUM_STATS_GPU_FREQUENCY);
  if (m != NULL)
    stats_set(stats, "gpu_sm_clock",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));

  m = intel_gpu_find_rt(row, XPUM_STATS_EU_ACTIVE);
  if (m != NULL)
    stats_set(stats, "sm_active",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));

  m = intel_gpu_find_rt(row, XPUM_STATS_MEMORY_BANDWIDTH);
  if (m != NULL)
    stats_set(stats, "gpu_dram_active",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));

  m = intel_gpu_find_rt(row, XPUM_STATS_PCIE_READ);
  if (m != NULL)
    stats_set(stats, "gpu_pcie_rx_bytes",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));
  else
    stats_set(stats, "gpu_pcie_rx_bytes", 0);

  m = intel_gpu_find_rt(row, XPUM_STATS_PCIE_WRITE);
  if (m != NULL)
    stats_set(stats, "gpu_pcie_tx_bytes",
              (unsigned long long) (intel_gpu_scaled_u64(m->value, m->scale) + 0.5));
  else
    stats_set(stats, "gpu_pcie_tx_bytes", 0);

  m = intel_gpu_find_rt(row, XPUM_STATS_FREQUENCY_THROTTLE_REASON_GPU);
  if (m != NULL)
    stats_set(stats, "clocks_event_reasons", (unsigned long long) m->value);
  else
    stats_set(stats, "clocks_event_reasons", 0);

  stats_set(stats, "gpu_count", (unsigned long long) gpu_count);
}

static void intel_gpu_publish_xe_link(struct stats *stats, xpum_device_id_t id)
{
  xpum_device_fabric_throughput_stats_t fabric[128];
  uint32_t count = (uint32_t) (sizeof(fabric) / sizeof(fabric[0]));
  uint64_t begin = 0;
  uint64_t end = 0;
  uint64_t rx = 0;
  uint64_t tx = 0;
  uint32_t i;

  if (stats == NULL)
    return;
  if (xpum_gpu_dyn_xpumGetFabricThroughputStats(id, fabric, &count, &begin, &end, 0) != XPUM_OK) {
    stats_set(stats, "gpu_xe_link_rx_bytes", 0);
    stats_set(stats, "gpu_xe_link_tx_bytes", 0);
    return;
  }
  for (i = 0; i < count; i++) {
    uint64_t v = fabric[i].accumulated;
    uint32_t scale = fabric[i].scale;

    if (scale == 0)
      scale = 1;
    v = v / scale;
    if (fabric[i].type == XPUM_FABRIC_THROUGHPUT_TYPE_RECEIVED_COUNTER)
      rx += v;
    else if (fabric[i].type == XPUM_FABRIC_THROUGHPUT_TYPE_TRANSMITTED_COUNTER)
      tx += v;
  }
  stats_set(stats, "gpu_xe_link_rx_bytes", rx);
  stats_set(stats, "gpu_xe_link_tx_bytes", tx);
}

static int intel_gpu_collect_one_rt(xpum_device_id_t id, xpum_device_realtime_metrics_t *out_row)
{
  xpum_device_realtime_metrics_t rows[8];
  uint32_t count = 0;
  uint32_t i;

  if (out_row == NULL)
    return -1;
  count = 0;
  if (xpum_gpu_dyn_xpumGetRealtimeMetrics(id, NULL, &count) != XPUM_OK && count == 0)
    return -1;
  if (count == 0)
    count = 1;
  if (count > (uint32_t) (sizeof(rows) / sizeof(rows[0])))
    count = (uint32_t) (sizeof(rows) / sizeof(rows[0]));
  if (xpum_gpu_dyn_xpumGetRealtimeMetrics(id, rows, &count) != XPUM_OK || count == 0)
    return -1;
  /* Prefer device-level row (not tile) when present. */
  for (i = 0; i < count; i++) {
    if (!rows[i].isTileData) {
      *out_row = rows[i];
      return 0;
    }
  }
  *out_row = rows[0];
  return 0;
}

static int intel_gpu_collect_one_stats_fallback(xpum_device_id_t id,
                                                xpum_device_realtime_metrics_t *out_row)
{
  xpum_device_stats_t rows[8];
  uint32_t count = 0;
  uint64_t begin = 0;
  uint64_t end = 0;
  uint32_t i;
  int j;

  if (out_row == NULL)
    return -1;
  memset(out_row, 0, sizeof(*out_row));
  count = 0;
  if (xpum_gpu_dyn_xpumGetStats(id, NULL, &count, &begin, &end, 0) != XPUM_OK && count == 0)
    return -1;
  if (count == 0)
    count = 1;
  if (count > (uint32_t) (sizeof(rows) / sizeof(rows[0])))
    count = (uint32_t) (sizeof(rows) / sizeof(rows[0]));
  if (xpum_gpu_dyn_xpumGetStats(id, rows, &count, &begin, &end, 0) != XPUM_OK || count == 0)
    return -1;
  for (i = 0; i < count; i++) {
    if (!rows[i].isTileData)
      break;
  }
  if (i >= count)
    i = 0;
  out_row->deviceId = rows[i].deviceId;
  out_row->isTileData = rows[i].isTileData;
  out_row->tileId = rows[i].tileId;
  out_row->count = 0;
  for (j = 0; j < rows[i].count && j < XPUM_STATS_MAX; j++) {
    out_row->dataList[out_row->count].metricsType = rows[i].dataList[j].metricsType;
    out_row->dataList[out_row->count].isCounter = rows[i].dataList[j].isCounter;
    out_row->dataList[out_row->count].value =
        rows[i].dataList[j].isCounter ? rows[i].dataList[j].accumulated
                                       : rows[i].dataList[j].avg;
    out_row->dataList[out_row->count].scale = rows[i].dataList[j].scale;
    out_row->count++;
  }
  return out_row->count > 0 ? 0 : -1;
}

static int intel_gpu_runtime_prepare(void)
{
  int count = 0;
  xpum_device_basic_info list[XPUM_MAX_NUM_DEVICES];
  int i;

  if (g_xpum_ready)
    return 0;
  intel_gpu_env_prepare();
  if (xpum_gpu_dyn_load() < 0) {
    ERROR("intel_gpu: cannot dlopen libxpum: %s\n", xpum_gpu_dyn_last_error());
    return -1;
  }
  if (xpum_gpu_dyn_xpumInit() != XPUM_OK) {
    ERROR("intel_gpu: xpumInit failed\n");
    return -1;
  }
  count = 0;
  if (xpum_gpu_dyn_xpumGetDeviceList(NULL, &count) != XPUM_OK && count <= 0) {
    ERROR("intel_gpu: xpumGetDeviceList count failed\n");
    (void) xpum_gpu_dyn_xpumShutdown();
    return -1;
  }
  if (count <= 0 || count > XPUM_MAX_NUM_DEVICES) {
    ERROR("intel_gpu: no XPUM devices (count=%d)\n", count);
    (void) xpum_gpu_dyn_xpumShutdown();
    return -1;
  }
  if (xpum_gpu_dyn_xpumGetDeviceList(list, &count) != XPUM_OK) {
    ERROR("intel_gpu: xpumGetDeviceList failed\n");
    (void) xpum_gpu_dyn_xpumShutdown();
    return -1;
  }
  g_device_count = count;
  for (i = 0; i < count; i++)
    g_device_ids[i] = list[i].deviceId;
  g_xpum_ready = 1;
  g_miss_streak = 0;
  return 0;
}

static void intel_gpu_collect(struct stats_type *type)
{
  int i;
  int ok_any = 0;

  if (type == NULL || !type->st_enabled)
    return;
  if (intel_gpu_runtime_prepare() < 0) {
    g_miss_streak++;
    if (g_miss_streak >= INTEL_GPU_MISS_STREAK_MAX) {
      TRACE("intel_gpu: disabling after prepare failures\n");
      type->st_enabled = 0;
    }
    return;
  }

  for (i = 0; i < g_device_count; i++) {
    char dev[16];
    struct stats *stats;
    xpum_device_realtime_metrics_t row;
    unsigned long long mem_total_mb = 0;

    snprintf(dev, sizeof(dev), "%d", (int) g_device_ids[i]);
    stats = get_current_stats(type, dev);
    if (stats == NULL)
      continue;
    (void) intel_gpu_lookup_mem_total_mb(g_device_ids[i], &mem_total_mb);
    memset(&row, 0, sizeof(row));
    if (intel_gpu_collect_one_rt(g_device_ids[i], &row) != 0) {
      if (intel_gpu_collect_one_stats_fallback(g_device_ids[i], &row) != 0)
        continue;
    }
    intel_gpu_publish_from_rt(stats, &row, mem_total_mb, g_device_count);
    intel_gpu_publish_xe_link(stats, g_device_ids[i]);
    ok_any = 1;
  }

  if (!ok_any) {
    g_miss_streak++;
    if (g_miss_streak >= INTEL_GPU_MISS_STREAK_MAX) {
      TRACE("intel_gpu: disabling after collect failures\n");
      type->st_enabled = 0;
    }
  } else {
    g_miss_streak = 0;
  }
}

struct stats_type intel_gpu_stats_type = {
  .st_collect = &intel_gpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
  .st_name = "intel_gpu",
};
