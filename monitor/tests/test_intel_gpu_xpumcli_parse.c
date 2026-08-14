/* Unit tests for intel_gpu xpumcli discovery/dump CSV parse (no live GPU). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "intel_gpu_xpumcli.h"
#include "stats.h"
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

  assert(samples[1].device_id == 1);
  assert(samples[1].has_gpu_util);
  assert(samples[1].gpu_util == 12.5);

  test_stats_stub_reset(&g_stub);
  intel_gpu_xpumcli_publish_sample(&g_dummy, &samples[0], 2);
  assert(test_stats_stub_find(&g_stub, "power_usage", &val) && val == 294ULL);
  assert(test_stats_stub_find(&g_stub, "temperature", &val) && val == 29ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_mem_used_mb", &val) && val == 1024ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_count", &val) && val == 2ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_pcie_rx_bytes", &val) && val == 0ULL);
}

int main(void)
{
  test_stats_stub_bind(&g_stub);
  test_parse_discovery();
  test_parse_dump_power();
  test_stats_stub_unbind();
  printf("test_intel_gpu_xpumcli_parse passed\n");
  return 0;
}
