/* Unit tests for intel_gpu collect/publish (xpum_gpu_dyn test hooks; no live GPU). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "intel_gpu.h"
#include "intel_gpu_xpum_helpers.h"
#include "intel_gpu_xpumcli.h"
#include "stats.h"
#include "test_stats_stub.h"
#include "xpum_gpu_dyn.h"

#ifdef INTEL_GPU_TEST_BUILD
void intel_gpu_test_reset(void);
#endif

/* Linked from intel_gpu.c; default NULL → xpumcli; tests force libxpum via env. */
char *intel_gpu_backend = NULL;

static struct test_stats_stub g_stub;
static struct stats g_dummy_stats;
static xpum_device_basic_info g_dev_list[1];
static int g_rt_fetch_calls;
static int g_stats_fetch_calls;
static enum {
  RT_SCENARIO_EMPTY,
  RT_SCENARIO_TILE_MERGE,
  RT_SCENARIO_MEM_TEMP,
  RT_SCENARIO_TILE_POWER_SUM,
  RT_SCENARIO_DEVICE_AVG_VS_TILE_SUM,
} g_rt_scenario = RT_SCENARIO_EMPTY;

static xpum_result_t fake_xpumInit(void)
{
  return XPUM_OK;
}

static xpum_result_t fake_xpumShutdown(void)
{
  return XPUM_OK;
}

static xpum_result_t fake_xpumGetDeviceList(xpum_device_basic_info deviceList[], int *count)
{
  if (count == NULL)
    return XPUM_GENERIC_ERROR;
  if (deviceList == NULL) {
    *count = 1;
    return XPUM_OK;
  }
  *count = 1;
  deviceList[0] = g_dev_list[0];
  return XPUM_OK;
}

static xpum_result_t fake_xpumGetDeviceProperties(xpum_device_id_t deviceId,
                                                  xpum_device_properties_t *pXpumProperties)
{
  int i;

  (void)deviceId;
  if (pXpumProperties == NULL)
    return XPUM_GENERIC_ERROR;
  memset(pXpumProperties, 0, sizeof(*pXpumProperties));
  i = 0;
  pXpumProperties->properties[i].name = XPUM_DEVICE_PROPERTY_MEMORY_PHYSICAL_SIZE_BYTE;
  snprintf(pXpumProperties->properties[i].value, sizeof(pXpumProperties->properties[i].value),
           "%llu", 137438953472ULL);
  i++;
  pXpumProperties->propertyLen = i;
  return XPUM_OK;
}

static void fill_empty_rt_rows(xpum_device_realtime_metrics_t rows[], uint32_t *count)
{
  rows[0].deviceId = 0;
  rows[0].isTileData = 0;
  rows[0].count = 0;
  *count = 1;
}

static void fill_tile_merge_rt_rows(xpum_device_realtime_metrics_t rows[], uint32_t *count)
{
  rows[0].deviceId = 0;
  rows[0].isTileData = 1;
  rows[0].tileId = 0;
  rows[0].count = 1;
  rows[0].dataList[0].metricsType = XPUM_STATS_GPU_UTILIZATION;
  rows[0].dataList[0].value = 500;
  rows[0].dataList[0].scale = 10;

  rows[1].deviceId = 0;
  rows[1].isTileData = 1;
  rows[1].tileId = 1;
  rows[1].count = 1;
  rows[1].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[1].dataList[0].value = 29400;
  rows[1].dataList[0].scale = 100;

  *count = 2;
}

static void fill_mem_temp_rt_row(xpum_device_realtime_metrics_t rows[], uint32_t *count)
{
  rows[0].deviceId = 0;
  rows[0].isTileData = 0;
  rows[0].count = 2;
  rows[0].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[0].dataList[0].value = 29000;
  rows[0].dataList[0].scale = 100;
  rows[0].dataList[1].metricsType = XPUM_STATS_MEMORY_TEMPERATURE;
  rows[0].dataList[1].value = 290;
  rows[0].dataList[1].scale = 10;
  *count = 1;
}

/* Two tiles each ~147 W — package must be sum (294), not one tile. */
static void fill_tile_power_sum_rows(xpum_device_realtime_metrics_t rows[], uint32_t *count)
{
  rows[0].deviceId = 0;
  rows[0].isTileData = 1;
  rows[0].tileId = 0;
  rows[0].count = 2;
  rows[0].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[0].dataList[0].value = 14700;
  rows[0].dataList[0].scale = 100;
  rows[0].dataList[1].metricsType = XPUM_STATS_GPU_FREQUENCY;
  rows[0].dataList[1].value = 1600;
  rows[0].dataList[1].scale = 1;

  rows[1].deviceId = 0;
  rows[1].isTileData = 1;
  rows[1].tileId = 1;
  rows[1].count = 1;
  rows[1].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[1].dataList[0].value = 14700;
  rows[1].dataList[0].scale = 100;

  *count = 2;
}

/*
 * Device row reports average-like ~147 W; tiles sum to ~294 W (xpumcli package).
 * Prefer the larger (tile sum).
 */
static void fill_device_avg_vs_tile_sum_rows(xpum_device_realtime_metrics_t rows[], uint32_t *count)
{
  rows[0].deviceId = 0;
  rows[0].isTileData = 0;
  rows[0].count = 1;
  rows[0].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[0].dataList[0].value = 14700;
  rows[0].dataList[0].scale = 100;

  rows[1].deviceId = 0;
  rows[1].isTileData = 1;
  rows[1].tileId = 0;
  rows[1].count = 1;
  rows[1].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[1].dataList[0].value = 14700;
  rows[1].dataList[0].scale = 100;

  rows[2].deviceId = 0;
  rows[2].isTileData = 1;
  rows[2].tileId = 1;
  rows[2].count = 1;
  rows[2].dataList[0].metricsType = XPUM_STATS_POWER;
  rows[2].dataList[0].value = 14700;
  rows[2].dataList[0].scale = 100;

  *count = 3;
}

static xpum_result_t fake_xpumGetRealtimeMetrics(xpum_device_id_t deviceId,
                                                 xpum_device_realtime_metrics_t dataList[],
                                                 uint32_t *count)
{
  (void)deviceId;
  if (count == NULL)
    return XPUM_GENERIC_ERROR;
  if (dataList == NULL) {
    if (g_rt_scenario == RT_SCENARIO_TILE_MERGE || g_rt_scenario == RT_SCENARIO_TILE_POWER_SUM)
      *count = 2u;
    else if (g_rt_scenario == RT_SCENARIO_DEVICE_AVG_VS_TILE_SUM)
      *count = 3u;
    else
      *count = 1u;
    return XPUM_OK;
  }
  g_rt_fetch_calls++;
  switch (g_rt_scenario) {
  case RT_SCENARIO_TILE_MERGE:
    fill_tile_merge_rt_rows(dataList, count);
    break;
  case RT_SCENARIO_MEM_TEMP:
    fill_mem_temp_rt_row(dataList, count);
    break;
  case RT_SCENARIO_TILE_POWER_SUM:
    fill_tile_power_sum_rows(dataList, count);
    break;
  case RT_SCENARIO_DEVICE_AVG_VS_TILE_SUM:
    fill_device_avg_vs_tile_sum_rows(dataList, count);
    break;
  case RT_SCENARIO_EMPTY:
  default:
    fill_empty_rt_rows(dataList, count);
    break;
  }
  return XPUM_OK;
}

static xpum_result_t fake_xpumGetStats(xpum_device_id_t deviceId, xpum_device_stats_t dataList[],
                                       uint32_t *count, uint64_t *begin, uint64_t *end,
                                       uint64_t sessionId)
{
  (void)deviceId;
  (void)begin;
  (void)end;
  (void)sessionId;
  if (count == NULL)
    return XPUM_GENERIC_ERROR;
  if (dataList == NULL) {
    *count = 1;
    return XPUM_OK;
  }
  g_stats_fetch_calls++;
  memset(dataList, 0, sizeof(*dataList));
  dataList[0].deviceId = 0;
  dataList[0].isTileData = 0;
  dataList[0].count = 1;
  dataList[0].dataList[0].metricsType = XPUM_STATS_POWER;
  dataList[0].dataList[0].isCounter = 0;
  dataList[0].dataList[0].avg = 28000;
  dataList[0].dataList[0].scale = 100;
  *count = 1;
  return XPUM_OK;
}

static xpum_result_t
fake_xpumGetFabricThroughputStats(xpum_device_id_t deviceId,
                                  xpum_device_fabric_throughput_stats_t dataList[], uint32_t *count,
                                  uint64_t *begin, uint64_t *end, uint64_t sessionId)
{
  (void)deviceId;
  (void)dataList;
  (void)begin;
  (void)end;
  (void)sessionId;
  if (count != NULL)
    *count = 0;
  return XPUM_GENERIC_ERROR;
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void)type;
  (void)dev;
  return &g_dummy_stats;
}

static void install_hooks(void)
{
  struct xpum_gpu_dyn_test_hooks hooks;

  memset(&hooks, 0, sizeof(hooks));
  hooks.xpumInit = fake_xpumInit;
  hooks.xpumShutdown = fake_xpumShutdown;
  hooks.xpumGetDeviceList = fake_xpumGetDeviceList;
  hooks.xpumGetDeviceProperties = fake_xpumGetDeviceProperties;
  hooks.xpumGetRealtimeMetrics = fake_xpumGetRealtimeMetrics;
  hooks.xpumGetStats = fake_xpumGetStats;
  hooks.xpumGetFabricThroughputStats = fake_xpumGetFabricThroughputStats;
  xpum_gpu_dyn_test_set_hooks(&hooks);
}

static void reset_test_state(void)
{
  /* Force libxpum path for existing dyn-hook tests (default collect is xpumcli). */
  assert(setenv("HPCPERFSTATS_INTEL_GPU_BACKEND", "libxpum", 1) == 0);
  xpum_gpu_dyn_unload();
  install_hooks();
#ifdef INTEL_GPU_TEST_BUILD
  intel_gpu_test_reset();
#endif
  test_stats_stub_reset(&g_stub);
  g_rt_fetch_calls = 0;
  g_stats_fetch_calls = 0;
  g_dev_list[0].deviceId = 0;
  g_dev_list[0].type = GPU;
}

static int g_init_calls;

static xpum_result_t counting_xpumInit(void)
{
  g_init_calls++;
  return XPUM_OK;
}

static int fake_xpumcli_capture_fail(char *const argv[], char *out, size_t out_cap)
{
  (void)argv;
  (void)out;
  (void)out_cap;
  return -1;
}

static void test_default_xpumcli_skips_xpumInit(void)
{
  struct xpum_gpu_dyn_test_hooks hooks;

  assert(unsetenv("HPCPERFSTATS_INTEL_GPU_BACKEND") == 0);
  intel_gpu_backend = NULL;
  xpum_gpu_dyn_unload();
  memset(&hooks, 0, sizeof(hooks));
  hooks.xpumInit = counting_xpumInit;
  hooks.xpumShutdown = fake_xpumShutdown;
  hooks.xpumGetDeviceList = fake_xpumGetDeviceList;
  hooks.xpumGetDeviceProperties = fake_xpumGetDeviceProperties;
  hooks.xpumGetRealtimeMetrics = fake_xpumGetRealtimeMetrics;
  hooks.xpumGetStats = fake_xpumGetStats;
  hooks.xpumGetFabricThroughputStats = fake_xpumGetFabricThroughputStats;
  xpum_gpu_dyn_test_set_hooks(&hooks);
#ifdef INTEL_GPU_TEST_BUILD
  intel_gpu_test_reset();
  intel_gpu_xpumcli_test_set_capture(fake_xpumcli_capture_fail);
#endif
  g_init_calls = 0;
  test_stats_stub_reset(&g_stub);
  intel_gpu_stats_type.st_enabled = 1;
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
  assert(g_init_calls == 0);
}

static void test_helper_tile_merge(void)
{
  xpum_device_realtime_metrics_t rows[2];
  xpum_device_realtime_metrics_t merged;
  uint32_t count = 2;

  fill_tile_merge_rt_rows(rows, &count);
  assert(!intel_gpu_row_is_usable(&rows[0]));
  intel_gpu_merge_tile_rows(rows, count, &merged);
  assert(intel_gpu_row_is_usable(&merged));
  assert(intel_gpu_row_has_metric(&merged, XPUM_STATS_POWER));
  assert(intel_gpu_row_has_metric(&merged, XPUM_STATS_GPU_UTILIZATION));
}

static void test_empty_rt_stats_fallback(void)
{
  unsigned long long val;

  reset_test_state();
  g_rt_scenario = RT_SCENARIO_EMPTY;
  intel_gpu_stats_type.st_enabled = 1;
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
  assert(g_stats_fetch_calls >= 1);
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 280ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_mem_total_mb", &val) && val == 131072ULL);
}

static void test_tile_merge_collect_power(void)
{
  unsigned long long val;

  reset_test_state();
  g_rt_scenario = RT_SCENARIO_TILE_MERGE;
  intel_gpu_stats_type.st_enabled = 1;
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
  assert(g_stats_fetch_calls == 0);
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 294ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_util", &val) && val == 50ULL);
}

static void test_mem_temp_publish(void)
{
  unsigned long long val;

  reset_test_state();
  g_rt_scenario = RT_SCENARIO_MEM_TEMP;
  intel_gpu_stats_type.st_enabled = 1;
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
  assert(g_stats_fetch_calls == 0);
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 290ULL);
  assert(test_stats_stub_find(&g_stub, "temperature", &val) && val == 29ULL);
}

static void test_helper_package_power_sum_tiles(void)
{
  xpum_device_realtime_metrics_t rows[2];
  uint32_t count = 2;

  fill_tile_power_sum_rows(rows, &count);
  assert(intel_gpu_package_power_watts(rows, count) == 294.0);
}

static void test_helper_package_power_prefer_tile_sum(void)
{
  xpum_device_realtime_metrics_t rows[3];
  uint32_t count = 3;

  fill_device_avg_vs_tile_sum_rows(rows, &count);
  assert(intel_gpu_package_power_watts(rows, count) == 294.0);
}

static void test_tile_power_sum_collect(void)
{
  unsigned long long val;

  reset_test_state();
  g_rt_scenario = RT_SCENARIO_TILE_POWER_SUM;
  intel_gpu_stats_type.st_enabled = 1;
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
  assert(g_stats_fetch_calls == 0);
  /* Must not publish a single tile (~147 W). */
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 294ULL);
}

static void test_device_avg_vs_tile_sum_collect(void)
{
  unsigned long long val;

  reset_test_state();
  g_rt_scenario = RT_SCENARIO_DEVICE_AVG_VS_TILE_SUM;
  intel_gpu_stats_type.st_enabled = 1;
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
  assert(g_stats_fetch_calls == 0);
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 294ULL);
}

int main(void)
{
  test_stats_stub_bind(&g_stub);
  test_helper_tile_merge();
  test_helper_package_power_sum_tiles();
  test_helper_package_power_prefer_tile_sum();
  test_empty_rt_stats_fallback();
  test_tile_merge_collect_power();
  test_mem_temp_publish();
  test_tile_power_sum_collect();
  test_device_avg_vs_tile_sum_collect();
  test_default_xpumcli_skips_xpumInit();
  test_stats_stub_unbind();
  printf("test_intel_gpu_publish passed\n");
  return 0;
}
