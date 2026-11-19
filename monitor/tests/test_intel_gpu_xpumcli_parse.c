/* Unit tests for intel_gpu xpumcli discovery/dump CSV parse (no live GPU). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "intel_gpu_xpumcli.h"
#include "stats.h"
#include "test_stats_missing_stub.h"
#include "test_stats_stub.h"

static struct test_stats_stub g_stub;
static struct stats g_dummy;

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void)type;
  (void)dev;
  return &g_dummy;
}

static void test_parse_discovery(void)
{
  static const char text[] = "+-----------+--------------------------------------------------------"
                             "------------------------------+\n"
                             "| Device ID | Device Information                                     "
                             "                              |\n"
                             "+-----------+--------------------------------------------------------"
                             "------------------------------+\n"
                             "| 0         | Device Name: Intel(R) Data Center GPU Max 1550         "
                             "                              |\n"
                             "|           | PCI BDF Address: 0000:4b:00.0                          "
                             "                              |\n"
                             "+-----------+--------------------------------------------------------"
                             "------------------------------+\n"
                             "| 1         | Device Name: Intel(R) Data Center GPU Max 1550         "
                             "                              |\n"
                             "+-----------+--------------------------------------------------------"
                             "------------------------------+\n";
  int ids[8];
  int n = 0;

  assert(intel_gpu_xpumcli_parse_discovery(text, ids, 8, &n) == 0);
  assert(n == 2);
  assert(ids[0] == 0);
  assert(ids[1] == 1);
}

static void test_parse_dump_power(void)
{
  static const char csv[] = "Timestamp, DeviceId, GPU Utilization (%), GPU Power (W), GPU Memory "
                            "Temperature (Celsius Degree), "
                            "GPU Memory Used (MiB), GPU EU Array Active (%), Throttle reason\n"
                            "13:50:00.877,    0,  N/A, 294.41, 28.50, 1024,  N/A, Not Throttled\n"
                            "13:50:00.877,    1,  12.5, 100.0, 30.00, 512,  5.0, Not Throttled\n";
  struct intel_gpu_xpumcli_sample samples[4];
  int n;
  unsigned long long val;

  n = intel_gpu_xpumcli_parse_dump_csv(csv, samples, 4);
  assert(n == 2);
  assert(samples[0].device_id == 0);
  assert(samples[0].has_power);
  assert(samples[0].power_w > 294.0 && samples[0].power_w < 295.0);
  assert(samples[0].has_temp);
  assert(samples[0].temp_c > 28.0 && samples[0].temp_c < 29.0);
  assert(samples[0].has_mem_used_mb);
  assert(samples[0].mem_used_mb == 1024.0);
  assert(!samples[0].has_gpu_util);

  /* No compute engine group column: NOT_PRESENT, not 0. */
  assert(!samples[0].has_compute_util);

  assert(samples[1].device_id == 1);
  assert(samples[1].has_gpu_util);
  assert(samples[1].gpu_util == 12.5);

  test_stats_stub_reset(&g_stub);
  test_stats_missing_reset();
  intel_gpu_xpumcli_publish_sample(&g_dummy, &samples[0], 2);
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 294ULL);
  assert(test_stats_stub_find(&g_stub, "temperature", &val) && val == 29ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_mem_used_mb", &val) && val == 1024ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_count", &val) && val == 2ULL);
  /* xpumcli has no cumulative PCIe / Xe Link counters: NOT_BUILT, never a published 0. */
  assert(!test_stats_stub_find(&g_stub, "gpu_pcie_rx_bytes", &val));
  assert(test_stats_missing_code("gpu_pcie_rx_bytes") == STATS_SENTINEL_NOT_BUILT);
  assert(test_stats_missing_code("gpu_xe_link_tx_bytes") == STATS_SENTINEL_NOT_BUILT);
  assert(test_stats_missing_code(TEST_STATS_MISSING_ALL_KEY) == STATS_SENTINEL_NOT_PRESENT);
}

/* Header of a real PVC c551-004 dump (xpumcli dump -m 0,9,10,11,31, XPUM 1.2.33). */
#define PVC_DUMP_HEADER                                                                            \
  "Timestamp, DeviceId, GPU Utilization (%), GPU EU Array Active (%), GPU EU Array Stall (%), "    \
  "GPU EU Array Idle (%), Compute engine group utilization (%)\n"

/* PVC c551-004 under xpumcli diag --stress (GFX firmware below the XPUM minimum): EU N/A. */
static void test_parse_dump_pvc_stress(void)
{
  static const char csv[] = PVC_DUMP_HEADER "16:33:59.867,    0, 99.24,  N/A,  N/A,  N/A, 99.24\n";
  struct intel_gpu_xpumcli_sample samples[4];
  unsigned long long val;

  assert(intel_gpu_xpumcli_parse_dump_csv(csv, samples, 4) == 1);
  assert(samples[0].device_id == 0);
  assert(samples[0].has_gpu_util && samples[0].gpu_util == 99.24);
  assert(samples[0].has_compute_util && samples[0].compute_util == 99.24);
  assert(!samples[0].has_eu_active);

  test_stats_stub_reset(&g_stub);
  test_stats_missing_reset();
  intel_gpu_xpumcli_publish_sample(&g_dummy, &samples[0], 4);
  assert(test_stats_stub_find(&g_stub, "compute_engine_util", &val) && val == 99ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_util", &val) && val == 99ULL);
  /* EU Array Active N/A: value sentinel, never a published 0. */
  assert(!test_stats_stub_find(&g_stub, "sm_active", &val));
  assert(test_stats_missing_code(TEST_STATS_MISSING_ALL_KEY) == STATS_SENTINEL_NOT_PRESENT);
}

/* Idle PVC: XPUM sometimes prints N/A for every column, GPU Utilization included. */
static void test_parse_dump_pvc_all_na(void)
{
  static const char csv[] = PVC_DUMP_HEADER "16:34:10.012,    0,  N/A,  N/A,  N/A,  N/A,  N/A\n";
  struct intel_gpu_xpumcli_sample samples[4];
  unsigned long long val;

  assert(intel_gpu_xpumcli_parse_dump_csv(csv, samples, 4) == 1);
  assert(samples[0].device_id == 0);
  assert(!samples[0].has_gpu_util);
  assert(!samples[0].has_compute_util);
  assert(!samples[0].has_eu_active);

  test_stats_stub_reset(&g_stub);
  test_stats_missing_reset();
  intel_gpu_xpumcli_publish_sample(&g_dummy, &samples[0], 4);
  assert(!test_stats_stub_find(&g_stub, "compute_engine_util", &val));
  assert(!test_stats_stub_find(&g_stub, "gpu_util", &val));
  assert(!test_stats_stub_find(&g_stub, "sm_active", &val));
  assert(test_stats_missing_code(TEST_STATS_MISSING_ALL_KEY) == STATS_SENTINEL_NOT_PRESENT);
  assert(test_stats_stub_find(&g_stub, "gpu_count", &val) && val == 4ULL);
}

/* Repeated DeviceId rows (tiles): compute engine group is the max over rows, one sample. */
static void test_parse_dump_tile_rows_max_compute(void)
{
  static const char csv[] = PVC_DUMP_HEADER "16:33:59.867,    0, 40.00,  N/A,  N/A,  N/A, 40.00\n"
                                            "16:33:59.867,    0, 99.24,  N/A,  N/A,  N/A, 99.24\n"
                                            "16:33:59.867,    0,  N/A,  N/A,  N/A,  N/A,  N/A\n"
                                            "16:33:59.867,    1,  N/A,  N/A,  N/A,  N/A, 12.00\n";
  struct intel_gpu_xpumcli_sample samples[4];

  assert(intel_gpu_xpumcli_parse_dump_csv(csv, samples, 4) == 2);
  assert(samples[0].device_id == 0);
  assert(samples[0].has_compute_util && samples[0].compute_util == 99.24);
  assert(samples[0].has_gpu_util && samples[0].gpu_util == 40.0); /* first row keeps the rest */
  assert(samples[1].device_id == 1);
  assert(samples[1].has_compute_util && samples[1].compute_util == 12.0);
  /* Full sample table: a repeated DeviceId still merges, a new one is dropped. */
  assert(intel_gpu_xpumcli_parse_dump_csv(csv, samples, 1) == 1);
  assert(samples[0].compute_util == 99.24);
}

/* The default dump argv requests metric 31 (Compute engine group utilization). */
static void test_dump_metrics_include_compute_engine_group(void)
{
  static const char metrics[] = "," INTEL_GPU_XPUMCLI_DUMP_METRICS ",";

  assert(strstr(metrics, ",31,") != NULL);
  assert(strstr(metrics, ",9,") != NULL);
}

int main(void)
{
  test_stats_stub_bind(&g_stub);
  test_parse_discovery();
  test_parse_dump_power();
  test_parse_dump_pvc_stress();
  test_parse_dump_pvc_all_na();
  test_parse_dump_tile_rows_max_compute();
  test_dump_metrics_include_compute_engine_group();
  test_stats_stub_unbind();
  printf("test_intel_gpu_xpumcli_parse passed\n");
  return 0;
}
