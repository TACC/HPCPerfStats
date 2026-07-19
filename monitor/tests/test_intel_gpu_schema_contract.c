#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "intel_gpu.h"

#define X SCHEMA_DEF
static const char intel_gpu_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

static void assert_present(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) != NULL);
}

static void assert_absent(const char *schema, const char *frag)
{
  assert(strstr(schema, frag) == NULL);
}

int main(void)
{
  assert_present(intel_gpu_schema_def, " gpu_util,");
  assert_present(intel_gpu_schema_def, " gpu_mem_util,");
  assert_present(intel_gpu_schema_def, " gpu_mem_total_mb,U=MB");
  assert_present(intel_gpu_schema_def, " gpu_mem_used_mb,U=MB");
  assert_present(intel_gpu_schema_def, " power_usage,U=W");
  assert_present(intel_gpu_schema_def, " temperature,U=C");
  assert_present(intel_gpu_schema_def, " gpu_sm_clock,");
  assert_present(intel_gpu_schema_def, " sm_active,");
  assert_present(intel_gpu_schema_def, " gpu_dram_active,");
  assert_present(intel_gpu_schema_def, " gpu_pcie_rx_bytes,E,W=64,U=B");
  assert_present(intel_gpu_schema_def, " gpu_pcie_tx_bytes,E,W=64,U=B");
  assert_present(intel_gpu_schema_def, " gpu_xe_link_rx_bytes,E,W=64,U=B");
  assert_present(intel_gpu_schema_def, " gpu_xe_link_tx_bytes,E,W=64,U=B");
  assert_present(intel_gpu_schema_def, " clocks_event_reasons,");
  assert_present(intel_gpu_schema_def, " gpu_count,");
  assert_absent(intel_gpu_schema_def, "gpu_nvlink");
  assert_absent(intel_gpu_schema_def, "fp64_active");
  printf("test_intel_gpu_schema_contract passed\n");
  return 0;
}
