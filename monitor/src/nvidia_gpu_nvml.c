/*
 * nvidia_gpu NVML + GPM source (see nvidia_gpu_nvml.h).
 *
 * Uses NVIDIA's vendored nvml.h (third_party/nvidia-nvml) for types and constants only; the
 * library is dlopen()ed. NVML header notice, carried as its license requires: Copyright
 * 1993-2026 NVIDIA Corporation. NVIDIA MAKES NO REPRESENTATION ABOUT THE SUITABILITY OF THIS
 * SOURCE CODE FOR ANY PURPOSE. IT IS PROVIDED "AS IS" WITHOUT EXPRESS OR IMPLIED WARRANTY OF ANY
 * KIND. U.S. Government End Users: the header is a "commercial item" (48 C.F.R. 2.101) provided
 * to the U.S. Government only as a commercial end item (48 C.F.R. 12.212, 227.7202-1 through
 * 227.7202-4). Full text at the top of third_party/nvidia-nvml/nvml.h.
 */
#include <dlfcn.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "nvml.h"

#include "dyn_lib_path.h"
#include "nvidia_gpu_nvml.h"
#include "nvidia_gpu_nvml_convert.h"

/* GPM metric -> dcgm_data_t ratio field (offset of a double) or PROF byte field. */
enum nvml_gpm_dst { GPM_DST_RATIO, GPM_DST_BYTES };

struct nvml_gpm_map {
  unsigned int metric;
  enum nvml_gpm_dst kind;
  size_t offset;
};

static const struct nvml_gpm_map g_gpm_map[] = {
    {NVML_GPM_METRIC_SM_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, sm_active)},
    {NVML_GPM_METRIC_SM_OCCUPANCY, GPM_DST_RATIO, offsetof(dcgm_data_t, sm_occupancy)},
    {NVML_GPM_METRIC_ANY_TENSOR_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, tensor_active)},
    {NVML_GPM_METRIC_IMMA_TENSOR_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, tensor_imma_active)},
    {NVML_GPM_METRIC_HMMA_TENSOR_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, tensor_hmma_active)},
    {NVML_GPM_METRIC_DFMA_TENSOR_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, tensor_dfma_active)},
    {NVML_GPM_METRIC_DRAM_BW_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, dram_active)},
    {NVML_GPM_METRIC_FP64_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, fp64_active)},
    {NVML_GPM_METRIC_FP32_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, fp32_active)},
    {NVML_GPM_METRIC_FP16_UTIL, GPM_DST_RATIO, offsetof(dcgm_data_t, fp16_active)},
    {NVML_GPM_METRIC_PCIE_TX_PER_SEC, GPM_DST_BYTES, offsetof(dcgm_data_t, prof_pcie_tx_bytes)},
    {NVML_GPM_METRIC_PCIE_RX_PER_SEC, GPM_DST_BYTES, offsetof(dcgm_data_t, prof_pcie_rx_bytes)},
    {NVML_GPM_METRIC_NVLINK_TOTAL_TX_PER_SEC, GPM_DST_BYTES,
     offsetof(dcgm_data_t, prof_nvlink_tx_bytes)},
    {NVML_GPM_METRIC_NVLINK_TOTAL_RX_PER_SEC, GPM_DST_BYTES,
     offsetof(dcgm_data_t, prof_nvlink_rx_bytes)},
};
#define NVML_GPM_NMAP (sizeof(g_gpm_map) / sizeof(g_gpm_map[0]))

struct nvml_gpu {
  nvidia_gpu_nvml_dev_t dev;
  nvidia_gpu_nvml_sample_t sample[2];
  int prev;          /* index of the older sample */
  long long prev_us; /* monotonic time of sample[prev] */
};

static struct nvidia_gpu_nvml_api g_api;
static const struct nvidia_gpu_nvml_api *g_test_api;
static void *g_lib;
static int g_ready;
static int g_inited;
static int g_ndev;
static struct nvml_gpu g_gpu[NVIDIA_GPU_NVML_MAX_DEV];
static nvmlGpmMetricsGet_t g_mg; /* ~13 KB, reused every fill (no hot-path allocation) */
static char g_err[160];

static void nvml_set_err(const char *what, nvidia_gpu_nvml_ret_t r)
{
  const char *s = (r != 0 && g_api.error_string != NULL) ? g_api.error_string(r) : NULL;

  if (r != 0)
    snprintf(g_err, sizeof(g_err), "%s: %s (%d)", what, s != NULL ? s : "error", r);
  else
    snprintf(g_err, sizeof(g_err), "%s", what);
}

const char *nvidia_gpu_nvml_last_error(void)
{
  return g_err[0] != '\0' ? g_err : "no error";
}

void nvidia_gpu_nvml_test_set_api(const struct nvidia_gpu_nvml_api *api)
{
  nvidia_gpu_nvml_cleanup();
  g_test_api = api;
}

static int nvml_bind(void *lib)
{
  /* Symbol names are the versioned exports the nvml.h macros map to. */
  struct {
    const char *name;
    void **slot;
    int optional;
  } syms[] = {
      {"nvmlInit_v2", (void **)&g_api.init, 0},
      {"nvmlShutdown", (void **)&g_api.shutdown, 0},
      {"nvmlErrorString", (void **)&g_api.error_string, 0},
      {"nvmlDeviceGetCount_v2", (void **)&g_api.device_count, 0},
      {"nvmlDeviceGetHandleByIndex_v2", (void **)&g_api.device_by_index, 0},
      {"nvmlDeviceGetClockInfo", (void **)&g_api.clock_info, 0},
      {"nvmlDeviceGetPowerUsage", (void **)&g_api.power_usage, 0},
      {"nvmlDeviceGetUtilizationRates", (void **)&g_api.utilization, 0},
      {"nvmlDeviceGetTemperature", (void **)&g_api.temperature, 1},
      {"nvmlDeviceGetMemoryInfo", (void **)&g_api.memory_info, 0},
      {"nvmlDeviceGetCurrentClocksEventReasons", (void **)&g_api.clocks_event_reasons, 1},
      {"nvmlDeviceGetPcieReplayCounter", (void **)&g_api.pcie_replay_counter, 1},
      {"nvmlGpmQueryDeviceSupport", (void **)&g_api.gpm_query_support, 0},
      {"nvmlGpmSampleAlloc", (void **)&g_api.gpm_sample_alloc, 0},
      {"nvmlGpmSampleFree", (void **)&g_api.gpm_sample_free, 0},
      {"nvmlGpmSampleGet", (void **)&g_api.gpm_sample_get, 0},
      {"nvmlGpmMetricsGet", (void **)&g_api.gpm_metrics_get, 0},
  };
  size_t i;

  for (i = 0; i < sizeof(syms) / sizeof(syms[0]); i++) {
    *syms[i].slot = dlsym(lib, syms[i].name);
    if (*syms[i].slot == NULL && !syms[i].optional) {
      nvml_set_err(syms[i].name, 0); /* driver too old for GPM */
      return -1;
    }
  }
  /* Drivers before CUDA 11.x name it ...ThrottleReasons (same signature and bit layout). */
  if (g_api.clocks_event_reasons == NULL)
    *(void **)&g_api.clocks_event_reasons = dlsym(lib, "nvmlDeviceGetCurrentClocksThrottleReasons");
  return 0;
}

/* dlopen libnvidia-ml once (process lifetime, like libdcgm); a disallowed override fail-closes. */
static int nvml_load(void)
{
  static const char *const defaults[] = {"libnvidia-ml.so.1", "libnvidia-ml.so"};
  const char *override = getenv("HPCPERFSTATS_NVML_LIB");
  size_t i;

  if (g_test_api != NULL) {
    g_api = *g_test_api;
    return 0;
  }
  if (g_lib == NULL) {
    if (override != NULL && override[0] != '\0') {
      if (!dyn_lib_path_allowed(override)) {
        nvml_set_err("HPCPERFSTATS_NVML_LIB path not allowed", 0);
        return -1;
      }
      g_lib = dlopen(override, RTLD_LAZY | RTLD_LOCAL);
    } else {
      for (i = 0; i < sizeof(defaults) / sizeof(defaults[0]) && g_lib == NULL; i++)
        g_lib = dlopen(defaults[i], RTLD_LAZY | RTLD_LOCAL);
    }
    if (g_lib == NULL) {
      nvml_set_err("libnvidia-ml not loadable", 0);
      return -1;
    }
  }
  memset(&g_api, 0, sizeof(g_api));
  return nvml_bind(g_lib);
}

void nvidia_gpu_nvml_cleanup(void)
{
  int i, k;

  for (i = 0; i < g_ndev; i++) {
    for (k = 0; k < 2; k++) {
      if (g_gpu[i].sample[k] != NULL && g_api.gpm_sample_free != NULL)
        (void)g_api.gpm_sample_free(g_gpu[i].sample[k]);
    }
  }
  memset(g_gpu, 0, sizeof(g_gpu));
  if (g_inited && g_api.shutdown != NULL)
    (void)g_api.shutdown();
  g_inited = 0;
  g_ndev = 0;
  g_ready = 0;
}

/* One GPU: handle, GPM support, two sample buffers, first sample. */
static int nvml_prepare_gpu(unsigned int i, long long now_us)
{
  nvmlGpmSupport_t sup;
  struct nvml_gpu *g = &g_gpu[i];
  nvidia_gpu_nvml_ret_t r;

  r = g_api.device_by_index(i, &g->dev);
  if (r != 0) {
    nvml_set_err("nvmlDeviceGetHandleByIndex", r);
    return -1;
  }
  memset(&sup, 0, sizeof(sup));
  sup.version = NVML_GPM_SUPPORT_VERSION;
  r = g_api.gpm_query_support(g->dev, &sup);
  if (r != 0 || !sup.isSupportedDevice) {
    snprintf(g_err, sizeof(g_err), "GPU %u has no GPM support (rc=%d)", i, r);
    return -1;
  }
  if ((r = g_api.gpm_sample_alloc(&g->sample[0])) != 0 ||
      (r = g_api.gpm_sample_alloc(&g->sample[1])) != 0) {
    nvml_set_err("nvmlGpmSampleAlloc", r);
    return -1;
  }
  r = g_api.gpm_sample_get(g->dev, g->sample[0]);
  if (r != 0) {
    nvml_set_err("nvmlGpmSampleGet", r);
    return -1;
  }
  g->prev = 0;
  g->prev_us = now_us;
  return 0;
}

int nvidia_gpu_nvml_prepare(void)
{
  unsigned int n = 0, i;
  nvidia_gpu_nvml_ret_t r;
  struct timespec ts;
  long long now_us = 0;

  if (g_ready)
    return 0;
  g_err[0] = '\0';
  if (nvml_load() != 0)
    return -1;
  r = g_api.init();
  if (r != 0) {
    nvml_set_err("nvmlInit_v2", r);
    return -1;
  }
  g_inited = 1;
  r = g_api.device_count(&n);
  if (r != 0 || n == 0) {
    if (r != 0)
      nvml_set_err("nvmlDeviceGetCount_v2", r);
    else
      nvml_set_err("NVML reports no GPUs", 0);
    nvidia_gpu_nvml_cleanup();
    return -1;
  }
  if (n > NVIDIA_GPU_NVML_MAX_DEV)
    n = NVIDIA_GPU_NVML_MAX_DEV;
  if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0)
    now_us = (long long)ts.tv_sec * 1000000LL + (long long)ts.tv_nsec / 1000LL;
  for (i = 0; i < n; i++) {
    g_ndev = (int)i + 1; /* so cleanup frees what this GPU allocated */
    if (nvml_prepare_gpu(i, now_us) != 0) {
      nvidia_gpu_nvml_cleanup();
      return -1;
    }
  }
  g_ndev = (int)n;
  g_ready = 1;
  return 0;
}

int nvidia_gpu_nvml_ndev(void)
{
  return g_ready ? g_ndev : 0;
}

static void nvml_fill_basic(nvidia_gpu_nvml_dev_t d, dcgm_data_t *row)
{
  nvmlUtilization_t util;
  nvmlMemory_t mem;
  unsigned int u = 0;
  unsigned long long reasons = 0;

  if (g_api.clock_info(d, NVML_CLOCK_SM, &u) == 0)
    row->sm_clock = (int64_t)u;
  if (g_api.power_usage(d, &u) == 0)
    row->power_usage = nvidia_gpu_nvml_mw_to_w(u);
  if (g_api.utilization(d, &util) == 0) {
    row->gpu_util = (int64_t)util.gpu;
    row->mem_util = (int64_t)util.memory;
  }
  if (g_api.temperature != NULL && g_api.temperature(d, NVML_TEMPERATURE_GPU, &u) == 0)
    row->temperature = (int64_t)u;
  if (g_api.memory_info(d, &mem) == 0) {
    row->fb_total_mb = nvidia_gpu_nvml_bytes_to_mib(mem.total);
    row->fb_used_mb = nvidia_gpu_nvml_bytes_to_mib(mem.used);
    row->fb_free_mb = nvidia_gpu_nvml_bytes_to_mib(mem.free);
  }
  if (g_api.clocks_event_reasons != NULL && g_api.clocks_event_reasons(d, &reasons) == 0)
    row->clocks_event_reasons = (int64_t)reasons;
  if (g_api.pcie_replay_counter != NULL && g_api.pcie_replay_counter(d, &u) == 0)
    row->pcie_replay_counter = (int64_t)u;
}

/* Metrics of one sample pair into row; per-metric failures leave that field 0. */
static void nvml_apply_metrics(dcgm_data_t *row)
{
  unsigned int m;

  for (m = 0; m < g_mg.numMetrics && m < NVML_GPM_NMAP; m++) {
    const struct nvml_gpm_map *map = &g_gpm_map[m];
    char *dst = (char *)row + map->offset;

    if (g_mg.metrics[m].nvmlReturn != NVML_SUCCESS)
      continue;
    if (map->kind == GPM_DST_RATIO)
      (void)nvidia_gpu_nvml_pct_to_ratio(g_mg.metrics[m].value, (double *)(void *)dst);
    else
      *(uint64_t *)(void *)dst = nvidia_gpu_nvml_mib_per_s_to_bytes(g_mg.metrics[m].value);
  }
}

int nvidia_gpu_nvml_fill(int idx, long long now_us, dcgm_data_t *row)
{
  struct nvml_gpu *g;
  nvidia_gpu_nvml_ret_t r;
  int cur;
  unsigned int m;

  if (!g_ready || idx < 0 || idx >= g_ndev || row == NULL)
    return -1;
  g = &g_gpu[idx];
  nvml_fill_basic(g->dev, row);
  /* The GPM window is the collect interval; a too-short gap keeps the old sample, PROF = 0. */
  if (now_us - g->prev_us < NVIDIA_GPU_NVML_GPM_MIN_GAP_US)
    return 0;
  cur = 1 - g->prev;
  r = g_api.gpm_sample_get(g->dev, g->sample[cur]);
  if (r != 0) {
    nvml_set_err("nvmlGpmSampleGet", r);
    return -1;
  }
  memset(&g_mg, 0,
         offsetof(nvmlGpmMetricsGet_t, metrics) + NVML_GPM_NMAP * sizeof(g_mg.metrics[0]));
  g_mg.version = NVML_GPM_METRICS_GET_VERSION;
  g_mg.numMetrics = (unsigned int)NVML_GPM_NMAP;
  g_mg.sample1 = g->sample[g->prev];
  g_mg.sample2 = g->sample[cur];
  for (m = 0; m < NVML_GPM_NMAP; m++)
    g_mg.metrics[m].metricId = g_gpm_map[m].metric;
  r = g_api.gpm_metrics_get(&g_mg);
  g->prev = cur;
  g->prev_us = now_us;
  if (r != 0) {
    nvml_set_err("nvmlGpmMetricsGet", r);
    return -1;
  }
  nvml_apply_metrics(row);
  return 0;
}
