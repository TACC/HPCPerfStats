#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "roofline_hw_peak.h"

#define X SCHEMA_DEF
static const char roofline_hw_peak_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

int main(void)
{
  assert(strcmp(ROOFLINE_HW_PEAK_ST_NAME, "host_roofline_peak") == 0);
  assert(strstr(roofline_hw_peak_schema_def, " cpu_peak_fp64_flops_per_s,U=FLOP/s") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " cpu_peak_dram_bw_bytes_per_s,U=B/s") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " gpu_peak_fp64_flops_per_s,U=FLOP/s") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " gpu_peak_mem_bw_bytes_per_s,U=B/s") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " gpu_peak_io_link_bw_bytes_per_s,U=B/s") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " cpu_peak_source") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " gpu_peak_source") != NULL);
  assert(strstr(roofline_hw_peak_schema_def, " peak_calc_version") != NULL);
  printf("test_roofline_hw_peak_schema passed\n");
  return 0;
}
