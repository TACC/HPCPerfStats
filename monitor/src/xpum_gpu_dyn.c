/* xpum_gpu_dyn — runtime dlopen of libxpum for intel_gpu (no link-time -lxpum). */
#include "xpum_gpu_dyn.h"

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define XPUM_GPU_DYN_SYM_LIST \
  X(xpumInit) \
  X(xpumShutdown) \
  X(xpumGetDeviceList) \
  X(xpumGetDeviceProperties) \
  X(xpumGetRealtimeMetrics) \
  X(xpumGetStats) \
  X(xpumGetFabricThroughputStats)

#define X(name) static __typeof__(name) *p_##name;
XPUM_GPU_DYN_SYM_LIST
#undef X

static void *g_xpum_handle;
static int g_xpum_loaded;
static char g_xpum_last_error[256];

struct xpum_gpu_dyn_test_hooks g_xpum_test_hooks_storage;
static struct xpum_gpu_dyn_test_hooks *g_xpum_test_hooks;
static int g_xpum_test_hooks_active;

static void xpum_gpu_dyn_set_error(const char *msg)
{
  if (msg == NULL)
    msg = "unknown error";
  snprintf(g_xpum_last_error, sizeof(g_xpum_last_error), "%s", msg);
}

const char *xpum_gpu_dyn_last_error(void)
{
  return g_xpum_last_error[0] != '\0' ? g_xpum_last_error : "xpum_gpu_dyn: no error recorded";
}

static int xpum_gpu_dyn_resolve_one(void *lib, const char *sym, void **out)
{
  void *fn;

  if (out == NULL)
    return -1;
  *out = NULL;
  fn = dlsym(lib, sym);
  if (fn == NULL) {
    xpum_gpu_dyn_set_error(sym);
    return -1;
  }
  *out = fn;
  return 0;
}

static int xpum_gpu_dyn_try_open(const char *path)
{
  void *h;

  if (path == NULL || path[0] == '\0')
    return -1;
  h = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
  if (h == NULL) {
    xpum_gpu_dyn_set_error(dlerror());
    return -1;
  }
  g_xpum_handle = h;
  return 0;
}

static int xpum_gpu_dyn_bind_symbols(void)
{
  void *lib = g_xpum_handle;

#define X(name) \
  if (xpum_gpu_dyn_resolve_one(lib, #name, (void **) &p_##name) < 0) \
    return -1;
  XPUM_GPU_DYN_SYM_LIST
#undef X
  return 0;
}

void xpum_gpu_dyn_test_set_hooks(const struct xpum_gpu_dyn_test_hooks *hooks)
{
  memset(&g_xpum_test_hooks_storage, 0, sizeof(g_xpum_test_hooks_storage));
  g_xpum_test_hooks = NULL;
  g_xpum_test_hooks_active = 0;
  if (hooks != NULL) {
    g_xpum_test_hooks_storage = *hooks;
    g_xpum_test_hooks = &g_xpum_test_hooks_storage;
    g_xpum_test_hooks_active = 1;
  }
}

int xpum_gpu_dyn_load(void)
{
  static const char *default_libs[] = {
    "/usr/lib64/libxpum.so",
    "libxpum.so.1",
    "libxpum.so",
    NULL
  };
  const char *override;
  size_t i;

  if (g_xpum_loaded)
    return 0;

  g_xpum_last_error[0] = '\0';
  override = getenv("HPCPERFSTATS_XPUM_LIB");
  if (override != NULL && override[0] != '\0') {
    if (xpum_gpu_dyn_try_open(override) < 0)
      return -1;
  } else {
    for (i = 0; default_libs[i] != NULL; i++) {
      g_xpum_last_error[0] = '\0';
      if (xpum_gpu_dyn_try_open(default_libs[i]) == 0)
        break;
    }
    if (g_xpum_handle == NULL)
      return -1;
  }

  if (xpum_gpu_dyn_bind_symbols() < 0) {
    dlclose(g_xpum_handle);
    g_xpum_handle = NULL;
    return -1;
  }

  g_xpum_loaded = 1;
  return 0;
}

int xpum_gpu_dyn_loaded(void)
{
  return g_xpum_loaded;
}

void xpum_gpu_dyn_unload(void)
{
  if (g_xpum_handle != NULL) {
    dlclose(g_xpum_handle);
    g_xpum_handle = NULL;
  }
  g_xpum_loaded = 0;
  g_xpum_test_hooks_active = 0;
  g_xpum_test_hooks = NULL;
#define X(name) p_##name = NULL;
  XPUM_GPU_DYN_SYM_LIST
#undef X
}

#define XPUM_GPU_DYN_CALL(name, ...) \
  (g_xpum_test_hooks_active && g_xpum_test_hooks != NULL && g_xpum_test_hooks->name != NULL \
       ? g_xpum_test_hooks->name(__VA_ARGS__) \
       : (p_##name != NULL ? p_##name(__VA_ARGS__) : XPUM_GENERIC_ERROR))

xpum_result_t xpum_gpu_dyn_xpumInit(void)
{
  if (!g_xpum_loaded && !g_xpum_test_hooks_active)
    return XPUM_GENERIC_ERROR;
  return XPUM_GPU_DYN_CALL(xpumInit);
}

xpum_result_t xpum_gpu_dyn_xpumShutdown(void)
{
  return XPUM_GPU_DYN_CALL(xpumShutdown);
}

xpum_result_t xpum_gpu_dyn_xpumGetDeviceList(xpum_device_basic_info deviceList[], int *count)
{
  return XPUM_GPU_DYN_CALL(xpumGetDeviceList, deviceList, count);
}

xpum_result_t xpum_gpu_dyn_xpumGetDeviceProperties(xpum_device_id_t deviceId,
                                                   xpum_device_properties_t *pXpumProperties)
{
  return XPUM_GPU_DYN_CALL(xpumGetDeviceProperties, deviceId, pXpumProperties);
}

xpum_result_t xpum_gpu_dyn_xpumGetRealtimeMetrics(xpum_device_id_t deviceId,
                                                  xpum_device_realtime_metrics_t dataList[],
                                                  uint32_t *count)
{
  return XPUM_GPU_DYN_CALL(xpumGetRealtimeMetrics, deviceId, dataList, count);
}

xpum_result_t xpum_gpu_dyn_xpumGetStats(xpum_device_id_t deviceId, xpum_device_stats_t dataList[],
                                        uint32_t *count, uint64_t *begin, uint64_t *end,
                                        uint64_t sessionId)
{
  return XPUM_GPU_DYN_CALL(xpumGetStats, deviceId, dataList, count, begin, end, sessionId);
}

xpum_result_t xpum_gpu_dyn_xpumGetFabricThroughputStats(
    xpum_device_id_t deviceId, xpum_device_fabric_throughput_stats_t dataList[], uint32_t *count,
    uint64_t *begin, uint64_t *end, uint64_t sessionId)
{
  return XPUM_GPU_DYN_CALL(xpumGetFabricThroughputStats, deviceId, dataList, count, begin, end,
                           sessionId);
}
