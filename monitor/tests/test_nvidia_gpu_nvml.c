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
#include "stats_sentinel.h"

#define STUB_MAX_GPU 4

static struct {
  unsigned int ngpu;
  int init_rc;
  int gpm_supported[STUB_MAX_GPU];
  int sample_get_rc;
  int metrics_get_rc;
  int fail_metric_id; /* metric whose nvmlReturn is set to an error */
  int nan_metric_id;  /* metric whose value is NaN */
  int allocs, frees, inits, shutdowns, sample_gets, metrics_gets;
  int cc_rc;    /* nvmlDeviceGetCudaComputeCapability */
  int attr_rc;  /* nvmlDeviceGetAttributes_v2 (MIG handles only on real drivers) */
  int cores_rc; /* nvmlDeviceGetNumGpuCores */
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
    case NVML_GPM_METRIC_INTEGER_UTIL:
      x->value = 51.0;
      break;
    case NVML_GPM_METRIC_DMMA_TENSOR_UTIL:
      x->value = 12.5;
      break;
    case NVML_GPM_METRIC_DFMA_TENSOR_UTIL:
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
    if ((int)x->metricId == S.nan_metric_id)
      x->value = NAN;
  }
  return S.metrics_get_rc;
}

static nvidia_gpu_nvml_ret_t st_cc(nvidia_gpu_nvml_dev_t d, int *major, int *minor)
{
  (void)d;
  *major = 9;
  *minor = 0;
  return S.cc_rc;
}
static nvidia_gpu_nvml_ret_t st_attr(nvidia_gpu_nvml_dev_t d, void *a)
{
  nvmlDeviceAttributes_t *attr = a;

  (void)d;
  attr->multiprocessorCount = 132;
  return S.attr_rc;
}
static nvidia_gpu_nvml_ret_t st_cores(nvidia_gpu_nvml_dev_t d, unsigned int *n)
{
  (void)d;
  *n = 16896;
  return S.cores_rc;
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
    .cuda_compute_capability = st_cc,
    .attributes = st_attr,
    .num_gpu_cores = st_cores,
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
  S.nan_metric_id = -1;
  S.attr_rc = NVML_ERROR_NOT_SUPPORTED;
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
  /* GPM pair -> DCGM units. */
  assert(fabs(row.sm_active - 0.9544) < 1e-9);
  assert(fabs(row.tensor_active - 0.8747) < 1e-9);
  assert(fabs(row.fp64_active - 0.9405) < 1e-9);
  assert(row.prof_pcie_tx_bytes == 1048576ULL);
  assert(row.prof_nvlink_tx_bytes == 265289728000ULL);
  assert(row.tensor_imma_active == 0.0);
  /* integer_active <- GPM INTEGER_UTIL (4), tensor_dmma_active <- DMMA_TENSOR_UTIL (8); DFMA (6)
   * stays on tensor_dfma_active. */
  assert(fabs(row.integer_active - 0.51) < 1e-9);
  assert(fabs(row.tensor_dmma_active - 0.125) < 1e-9);
  assert(fabs(row.tensor_dfma_active - 0.9405) < 1e-9);
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
  /* Right after prepare (< 150 ms): no second sample, basic fields filled. */
  assert(nvidia_gpu_nvml_fill(0, t0, &row) == 0);
  assert(S.sample_gets == 1 && S.metrics_gets == 0);
  assert(row.sm_active == 0.0 && row.power_usage > 0.0);
  /* Next collect a sample interval later uses the pair; the one after alternates buffers. */
  assert(nvidia_gpu_nvml_fill(0, t0 + 60000000LL, &row) == 0);
  assert(nvidia_gpu_nvml_fill(0, t0 + 120000000LL, &row) == 0);
  assert(S.sample_gets == 3 && S.metrics_gets == 2);
  nvidia_gpu_nvml_cleanup();
}

static unsigned int miss_code(const dcgm_data_t *row, const char *key)
{
  for (int i = 0; i < row->nmiss; i++)
    if (strcmp(row->miss[i].key, key) == 0)
      return row->miss[i].code;
  return 0;
}

/* A failed GPM metric is READ_FAILED and a non-finite one INVALID_VALUE on exactly its key; a
 * metric that read 0 keeps the real 0 with no mark. */
static void test_failed_and_nonfinite_metrics_are_marked(void)
{
  dcgm_data_t row;

  reset_stub(1);
  S.fail_metric_id = NVML_GPM_METRIC_SM_UTIL;
  S.nan_metric_id = NVML_GPM_METRIC_PCIE_TX_PER_SEC;
  assert(nvidia_gpu_nvml_prepare() == 0);
  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(0, mono_us() + 1000000LL, &row) == 0);
  assert(miss_code(&row, "sm_active") == STATS_SENTINEL_READ_FAILED);
  assert(miss_code(&row, "gpu_pcie_tx_bytes") == STATS_SENTINEL_INVALID_VALUE);
  assert(miss_code(&row, "tensor_active") == 0 && fabs(row.tensor_active - 0.8747) < 1e-9);
  assert(miss_code(&row, "fp16_active") == 0 && row.fp16_active == 0.0);
  assert(miss_code(&row, "gpu_pcie_rx_bytes") == 0 && row.prof_pcie_rx_bytes == 0ULL);
  nvidia_gpu_nvml_cleanup();
}

/* gpm_short_gap: a collect closer than NVIDIA_GPU_NVML_GPM_MIN_GAP_US to the first GPM sample
 * (right after prepare: no window computed yet) must mark every GPM key NOT_UPDATED (it went out as
 * a real 0); NVML getter keys keep their values. The next full-gap collect reads the window. */
static void test_gpm_short_gap_marks_prof_not_updated(void)
{
  static const char *const gpm_keys[] = {
      "sm_active",          "sm_occupancy",       "tensor_active",       "tensor_imma_active",
      "tensor_hmma_active", "tensor_dfma_active", "tensor_dmma_active",  "integer_active",
      "gpu_dram_active",    "fp64_active",        "fp32_active",         "fp16_active",
      "gpu_pcie_tx_bytes",  "gpu_pcie_rx_bytes",  "gpu_nvlink_tx_bytes", "gpu_nvlink_rx_bytes"};
  dcgm_data_t row;
  long long t0;
  size_t i;

  reset_stub(1);
  assert(nvidia_gpu_nvml_prepare() == 0);
  t0 = mono_us();
  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(0, t0, &row) == 0);
  assert(S.metrics_gets == 0);
  for (i = 0; i < sizeof(gpm_keys) / sizeof(gpm_keys[0]); i++)
    assert(miss_code(&row, gpm_keys[i]) == STATS_SENTINEL_NOT_UPDATED);
  assert(miss_code(&row, "power_usage") == 0 && miss_code(&row, "gpu_util") == 0);
  assert(row.nmiss == (int)(sizeof(gpm_keys) / sizeof(gpm_keys[0])) + 1); /* + replay counter */

  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(0, t0 + 60000000LL, &row) == 0);
  assert(S.metrics_gets == 1);
  assert(row.nmiss == 1 && miss_code(&row, "gpu_pcie_replay_counter") != 0);
  nvidia_gpu_nvml_cleanup();
}

/* gpm_back_to_back: after one full window, a second collect inside the min gap (the sample timer
 * re-firing around one boundary) must republish the window that just closed, failed-metric codes
 * included, with no NOT_UPDATED and no new GPM sample. 3.1-1 returned every PROF key 0 here
 * (GH200 c642-041: every other fast sample under load). */
static void test_gpm_back_to_back_republishes_last_window(void)
{
  dcgm_data_t first;
  dcgm_data_t row;
  long long t1;

  reset_stub(1);
  S.fail_metric_id = NVML_GPM_METRIC_SM_OCCUPANCY;
  assert(nvidia_gpu_nvml_prepare() == 0);
  t1 = mono_us() + 30000000LL;
  memset(&first, 0, sizeof(first));
  assert(nvidia_gpu_nvml_fill(0, t1, &first) == 0);
  assert(S.metrics_gets == 1);

  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(0, t1 + 40000LL, &row) == 0);
  assert(S.sample_gets == 2 && S.metrics_gets == 1);
  assert(fabs(row.sm_active - 0.9544) < 1e-9);
  assert(fabs(row.tensor_active - 0.8747) < 1e-9);
  assert(fabs(row.fp64_active - 0.9405) < 1e-9);
  assert(row.prof_pcie_tx_bytes == 1048576ULL && row.prof_nvlink_tx_bytes == 265289728000ULL);
  assert(miss_code(&row, "sm_active") == 0 && miss_code(&row, "fp16_active") == 0);
  assert(miss_code(&row, "sm_occupancy") == STATS_SENTINEL_READ_FAILED);
  assert(row.nmiss == first.nmiss);

  /* The kept sample still anchors the next window at its own time. */
  assert(nvidia_gpu_nvml_fill(0, t1 + 30000000LL, &row) == 0);
  assert(S.sample_gets == 3 && S.metrics_gets == 2);
  nvidia_gpu_nvml_cleanup();
}

static void test_any_gpu_without_gpm_uses_dcgm(void)
{
  reset_stub(4);
  S.gpm_supported[3] = 0; /* e.g. a mixed node: whole node stays on DCGM */
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

/* A failed INTEGER_UTIL / DMMA metric is READ_FAILED on exactly its key (integer_active / dmma). */
static void test_integer_active_and_dmma_metric_failures_marked(void)
{
  dcgm_data_t row;

  reset_stub(1);
  S.fail_metric_id = NVML_GPM_METRIC_DMMA_TENSOR_UTIL;
  S.nan_metric_id = NVML_GPM_METRIC_INTEGER_UTIL;
  assert(nvidia_gpu_nvml_prepare() == 0);
  memset(&row, 0, sizeof(row));
  assert(nvidia_gpu_nvml_fill(0, mono_us() + 1000000LL, &row) == 0);
  assert(miss_code(&row, "tensor_dmma_active") == STATS_SENTINEL_READ_FAILED);
  assert(miss_code(&row, "integer_active") == STATS_SENTINEL_INVALID_VALUE);
  assert(miss_code(&row, "tensor_dfma_active") == 0);
  nvidia_gpu_nvml_cleanup();
}

/* Device shape for gpu_flops: CC and SM count from NVML on both paths. The probe runs without
 * GPM (the A100 DCGM path), keeps the shape after its own shutdown, and nvmlDeviceGetAttributes
 * (MIG handles only) failing leaves the core count for the estimate to divide. */
static void test_shape_probe_both_paths(void)
{
  dcgm_data_t row;

  reset_stub(2);
  S.gpm_supported[1] = 0;
  assert(nvidia_gpu_nvml_probe_shapes() == 2);
  assert(S.inits == 1 && S.shutdowns == 1);
  assert(nvidia_gpu_nvml_prepare() == -1); /* DCGM path */
  memset(&row, 0, sizeof(row));
  nvidia_gpu_nvml_row_shape(1, &row);
  assert(row.cc_major == 9 && row.cc_minor == 0);
  assert(row.sm_count == 0 && row.gpu_cores == 16896);

  reset_stub(1);
  S.attr_rc = 0;
  assert(nvidia_gpu_nvml_probe_shapes() == 1);
  memset(&row, 0, sizeof(row));
  nvidia_gpu_nvml_row_shape(0, &row);
  assert(row.sm_count == 132);

  /* Failed queries and out-of-range indices leave the shape unknown (0). */
  reset_stub(1);
  S.cc_rc = NVML_ERROR_NOT_SUPPORTED;
  S.cores_rc = NVML_ERROR_NOT_SUPPORTED;
  assert(nvidia_gpu_nvml_probe_shapes() == 1);
  memset(&row, 0, sizeof(row));
  nvidia_gpu_nvml_row_shape(0, &row);
  assert(row.cc_major == 0 && row.cc_minor == 0 && row.sm_count == 0 && row.gpu_cores == 0);
  row.cc_major = 5;
  nvidia_gpu_nvml_row_shape(7, &row);
  nvidia_gpu_nvml_row_shape(-1, &row);
  assert(row.cc_major == 0);
  nvidia_gpu_nvml_row_shape(0, NULL);

  reset_stub(1);
  S.init_rc = NVML_ERROR_DRIVER_NOT_LOADED;
  assert(nvidia_gpu_nvml_probe_shapes() == -1);
  memset(&row, 0, sizeof(row));
  nvidia_gpu_nvml_row_shape(0, &row);
  assert(row.cc_major == 0);
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
  test_failed_and_nonfinite_metrics_are_marked();
  test_gpm_short_gap_marks_prof_not_updated();
  test_gpm_back_to_back_republishes_last_window();
  test_any_gpu_without_gpm_uses_dcgm();
  test_init_failure_and_no_gpus();
  test_gpm_failure_mid_run_reports_fallback();
  test_integer_active_and_dmma_metric_failures_marked();
  test_shape_probe_both_paths();
  test_disallowed_lib_override_fails_closed();
  printf("test_nvidia_gpu_nvml passed\n");
  return 0;
}
