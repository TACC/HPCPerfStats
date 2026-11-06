/* nvidia_gpu NVML + GPM source with a stubbed NVML API (no driver, no GPU): source choice,
 * GPM pair handling, converters on the row, failure fallbacks, loader path check. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "nvml.h"

#include "nvidia_gpu_nvml.h"

#define STUB_MAX_GPU 4

static struct {
  unsigned int ngpu;
  int init_rc;
  int gpm_supported[STUB_MAX_GPU];
  int sample_get_rc;
  int metrics_get_rc;
  int fail_metric_id; /* metric whose nvmlReturn is set to an error */
  int allocs, frees, inits, shutdowns, sample_gets, metrics_gets;
} S;

static int g_sample_slots[64];

static nvidia_gpu_nvml_ret_t st_init(void)
{
  S.inits++;
  return S.init_rc;
}
static nvidia_gpu_nvml_ret_t st_shutdown(void)
{
  S.shutdowns++;
  return 0;
}
static const char *st_err(nvidia_gpu_nvml_ret_t r)
{
  (void)r;
  return "stub error";
}
static nvidia_gpu_nvml_ret_t st_count(unsigned int *n)
{
  *n = S.ngpu;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_by_index(unsigned int i, nvidia_gpu_nvml_dev_t *d)
{
  *d = (nvidia_gpu_nvml_dev_t)(uintptr_t)(i + 1);
  return 0;
}
static unsigned int dev_idx(nvidia_gpu_nvml_dev_t d)
{
  return (unsigned int)(uintptr_t)d - 1;
}
static nvidia_gpu_nvml_ret_t st_clock(nvidia_gpu_nvml_dev_t d, int type, unsigned int *mhz)
{
  assert(type == NVML_CLOCK_SM);
  *mhz = 1980 + dev_idx(d);
  return 0;
}
static nvidia_gpu_nvml_ret_t st_power(nvidia_gpu_nvml_dev_t d, unsigned int *mw)
{
  *mw = 151250 + 1000 * dev_idx(d);
  return 0;
}
static nvidia_gpu_nvml_ret_t st_util(nvidia_gpu_nvml_dev_t d, void *u)
{
  nvmlUtilization_t *util = u;

  (void)d;
  util->gpu = 97;
  util->memory = 41;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_temp(nvidia_gpu_nvml_dev_t d, int sensor, unsigned int *c)
{
  (void)d;
  assert(sensor == NVML_TEMPERATURE_GPU);
  *c = 38;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_mem(nvidia_gpu_nvml_dev_t d, void *m)
{
  nvmlMemory_t *mem = m;

  (void)d;
  mem->total = 193273528320ULL;
  mem->used = 1048576ULL * 600;
  mem->free = mem->total - mem->used;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_reasons(nvidia_gpu_nvml_dev_t d, unsigned long long *r)
{
  (void)d;
  *r = 0x4ULL;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_replay(nvidia_gpu_nvml_dev_t d, unsigned int *v)
{
  (void)d;
  (void)v;
  return NVML_ERROR_NOT_SUPPORTED; /* field must stay 0, row still valid */
}
static nvidia_gpu_nvml_ret_t st_gpm_support(nvidia_gpu_nvml_dev_t d, void *s)
{
  nvmlGpmSupport_t *sup = s;

  assert(sup->version == NVML_GPM_SUPPORT_VERSION);
  sup->isSupportedDevice = (unsigned int)S.gpm_supported[dev_idx(d)];
  return 0;
}
static nvidia_gpu_nvml_ret_t st_alloc(nvidia_gpu_nvml_sample_t *s)
{
  *s = &g_sample_slots[S.allocs % 64];
  S.allocs++;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_free(nvidia_gpu_nvml_sample_t s)
{
  (void)s;
  S.frees++;
  return 0;
}
static nvidia_gpu_nvml_ret_t st_sample_get(nvidia_gpu_nvml_dev_t d, nvidia_gpu_nvml_sample_t s)
{
  (void)d;
  (void)s;
  S.sample_gets++;
  return S.sample_get_rc;
}
static nvidia_gpu_nvml_ret_t st_metrics_get(void *p)
{
  nvmlGpmMetricsGet_t *mg = p;
  unsigned int m;

  S.metrics_gets++;
  assert(mg->version == NVML_GPM_METRICS_GET_VERSION);
  assert(mg->sample1 != NULL && mg->sample2 != NULL && mg->sample1 != mg->sample2);
  for (m = 0; m < mg->numMetrics; m++) {
    nvmlGpmMetric_t *x = &mg->metrics[m];

    x->nvmlReturn = ((int)x->metricId == S.fail_metric_id) ? NVML_ERROR_NOT_SUPPORTED : 0;
    switch (x->metricId) {
    case NVML_GPM_METRIC_SM_UTIL:
      x->value = 95.44;
      break;
    case NVML_GPM_METRIC_ANY_TENSOR_UTIL:
      x->value = 87.47;
      break;
    case NVML_GPM_METRIC_FP64_UTIL:
      x->value = 94.05;
      break;
    case NVML_GPM_METRIC_PCIE_TX_PER_SEC:
      x->value = 1.0;
      break;
    case NVML_GPM_METRIC_NVLINK_TOTAL_TX_PER_SEC:
      x->value = 253000.0;
      break;
    default:
      x->value = 0.0;
      break;
    }
  }
  return S.metrics_get_rc;
}

static const struct nvidia_gpu_nvml_api g_stub = {
    .init = st_init,
    .shutdown = st_shutdown,
    .error_string = st_err,
    .device_count = st_count,
    .device_by_index = st_by_index,
    .clock_info = st_clock,
    .power_usage = st_power,
    .utilization = st_util,
    .temperature = st_temp,
    .memory_info = st_mem,
    .clocks_event_reasons = st_reasons,
    .pcie_replay_counter = st_replay,
    .gpm_query_support = st_gpm_support,
    .gpm_sample_alloc = st_alloc,
    .gpm_sample_free = st_free,
    .gpm_sample_get = st_sample_get,
    .gpm_metrics_get = st_metrics_get,
};

static long long mono_us(void)
{
  struct timespec ts;

  assert(clock_gettime(CLOCK_MONOTONIC, &ts) == 0);
  return (long long)ts.tv_sec * 1000000LL + (long long)ts.tv_nsec / 1000LL;
}

static void reset_stub(unsigned int ngpu)
{
  unsigned int i;

  memset(&S, 0, sizeof(S));
  S.ngpu = ngpu;
  S.fail_metric_id = -1;
  for (i = 0; i < STUB_MAX_GPU; i++)
    S.gpm_supported[i] = 1;
  nvidia_gpu_nvml_test_set_api(&g_stub);
}

static void test_all_gpm_takes_nvml_path_and_fills_row(void)
{
  dcgm_data_t row;

  reset_stub(4);
  assert(nvidia_gpu_nvml_prepare() == 0);
  assert(nvidia_gpu_nvml_ndev() == 4);
  assert(S.allocs == 8 && S.sample_gets == 4); /* first sample taken in prepare */
  assert(nvidia_gpu_nvml_prepare() == 0);      /* idempotent */
  assert(S.inits == 1);

  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(2, mono_us() + 60000000LL, &row) == 0);
  assert(row.sm_clock == 1982);
  assert(fabs(row.power_usage - 153.25) < 1e-9);
  assert(row.gpu_util == 97 && row.mem_util == 41);
  assert(row.temperature == 38);
  assert(row.fb_total_mb == 184320 && row.fb_used_mb == 600);
  assert(row.clocks_event_reasons == 4);
  assert(row.pcie_replay_counter == 0);
  /* GPM pair -> DCGM units (finding 21). */
  assert(fabs(row.sm_active - 0.9544) < 1e-9);
  assert(fabs(row.tensor_active - 0.8747) < 1e-9);
  assert(fabs(row.fp64_active - 0.9405) < 1e-9);
  assert(row.prof_pcie_tx_bytes == 1048576ULL);
  assert(row.prof_nvlink_tx_bytes == 265289728000ULL);
  assert(row.tensor_imma_active == 0.0);
  assert(S.metrics_gets == 1);

  nvidia_gpu_nvml_cleanup();
  assert(S.frees == S.allocs && S.shutdowns == 1);
  assert(nvidia_gpu_nvml_ndev() == 0);
}

static void test_short_gap_skips_gpm_but_keeps_row(void)
{
  dcgm_data_t row;
  long long t0;

  reset_stub(1);
  assert(nvidia_gpu_nvml_prepare() == 0);
  t0 = mono_us();
  memset(&row, 0, sizeof(row));
  /* Right after prepare (< 150 ms): no second sample, PROF stays 0, basic fields filled. */
  assert(nvidia_gpu_nvml_fill(0, t0, &row) == 0);
  assert(S.sample_gets == 1 && S.metrics_gets == 0);
  assert(row.sm_active == 0.0 && row.power_usage > 0.0);
  /* Next collect a sample interval later uses the pair; the one after alternates buffers. */
  assert(nvidia_gpu_nvml_fill(0, t0 + 60000000LL, &row) == 0);
  assert(nvidia_gpu_nvml_fill(0, t0 + 120000000LL, &row) == 0);
  assert(S.sample_gets == 3 && S.metrics_gets == 2);
  nvidia_gpu_nvml_cleanup();
}

static void test_failed_metric_leaves_field_zero(void)
{
  dcgm_data_t row;

  reset_stub(1);
  S.fail_metric_id = NVML_GPM_METRIC_SM_UTIL;
  assert(nvidia_gpu_nvml_prepare() == 0);
  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(0, mono_us() + 1000000LL, &row) == 0);
  assert(row.sm_active == 0.0);
  assert(fabs(row.tensor_active - 0.8747) < 1e-9);
  nvidia_gpu_nvml_cleanup();
}

static void test_any_gpu_without_gpm_uses_dcgm(void)
{
  reset_stub(4);
  S.gpm_supported[3] = 0; /* e.g. a mixed node; D14: whole node stays on DCGM */
  assert(nvidia_gpu_nvml_prepare() == -1);
  assert(nvidia_gpu_nvml_ndev() == 0);
  assert(strstr(nvidia_gpu_nvml_last_error(), "GPU 3 has no GPM") != NULL);
  assert(S.frees == S.allocs && S.shutdowns == 1);
}

static void test_init_failure_and_no_gpus(void)
{
  reset_stub(2);
  S.init_rc = NVML_ERROR_DRIVER_NOT_LOADED;
  assert(nvidia_gpu_nvml_prepare() == -1);
  assert(S.shutdowns == 0);
  assert(strstr(nvidia_gpu_nvml_last_error(), "nvmlInit_v2") != NULL);

  reset_stub(0);
  assert(nvidia_gpu_nvml_prepare() == -1);
  assert(S.shutdowns == 1);
}

static void test_gpm_failure_mid_run_reports_fallback(void)
{
  dcgm_data_t row;

  reset_stub(2);
  assert(nvidia_gpu_nvml_prepare() == 0);
  S.sample_get_rc = NVML_ERROR_GPU_IS_LOST;
  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(1, mono_us() + 1000000LL, &row) == -1);
  assert(strstr(nvidia_gpu_nvml_last_error(), "nvmlGpmSampleGet") != NULL);
  S.sample_get_rc = 0;
  S.metrics_get_rc = NVML_ERROR_UNKNOWN;
  assert(nvidia_gpu_nvml_fill(0, mono_us() + 2000000LL, &row) == -1);
  /* Bad index / NULL row / not prepared. */
  assert(nvidia_gpu_nvml_fill(5, 0, &row) == -1);
  assert(nvidia_gpu_nvml_fill(0, 0, NULL) == -1);
  nvidia_gpu_nvml_cleanup();
  assert(nvidia_gpu_nvml_fill(0, 0, &row) == -1);
  assert(S.frees == S.allocs);
}

static void test_disallowed_lib_override_fails_closed(void)
{
  nvidia_gpu_nvml_test_set_api(NULL);
  assert(setenv("HPCPERFSTATS_NVML_LIB", "/tmp/libnvidia-ml.so.1", 1) == 0);
  assert(nvidia_gpu_nvml_prepare() == -1);
  assert(strstr(nvidia_gpu_nvml_last_error(), "not allowed") != NULL);
  assert(setenv("HPCPERFSTATS_NVML_LIB", "/usr/lib64/../tmp/libnvidia-ml.so.1", 1) == 0);
  assert(nvidia_gpu_nvml_prepare() == -1);
  assert(strstr(nvidia_gpu_nvml_last_error(), "not allowed") != NULL);
  assert(unsetenv("HPCPERFSTATS_NVML_LIB") == 0);
}

int main(void)
{
  test_all_gpm_takes_nvml_path_and_fills_row();
  test_short_gap_skips_gpm_but_keeps_row();
  test_failed_metric_leaves_field_zero();
  test_any_gpu_without_gpm_uses_dcgm();
  test_init_failure_and_no_gpus();
  test_gpm_failure_mid_run_reports_fallback();
  test_disallowed_lib_override_fails_closed();
  printf("test_nvidia_gpu_nvml passed\n");
  return 0;
}
