#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "nvidia_gpu.h"

#define X SCHEMA_DEF
static const char nvidia_schema_def[] = JOIN(KEYS);
#undef X
#undef KEYS

#include "amd_gpu.h"

#define X SCHEMA_DEF
static const char amd_schema_def[] = JOIN(KEYS);
#undef X

static void assert_common_gpu_roofline_keys(const char *schema_def)
{
  assert(strstr(schema_def, " gpu_flops_rate,U=FLOP/s") != NULL);
  assert(strstr(schema_def, " gpu_mem_bw_bytes_rate,U=B/s") != NULL);
  assert(strstr(schema_def, " gpu_flops,E,W=64,U=FLOP") != NULL);
  assert(strstr(schema_def, " gpu_mem_read_bytes,E,W=64,U=B") != NULL);
  assert(strstr(schema_def, " gpu_mem_write_bytes,E,W=64,U=B") != NULL);
  assert(strstr(schema_def, " gpu_mem_total_bytes,E,W=64,U=B") != NULL);
}

int main(void)
{
  assert(strstr(nvidia_schema_def, " module_power_usage,U=W") != NULL);
  assert(strstr(nvidia_schema_def, " sysio_power_usage,U=W") != NULL);
  assert_common_gpu_roofline_keys(nvidia_schema_def);
  assert_common_gpu_roofline_keys(amd_schema_def);
  printf("test_gpu_schema_contract passed\n");
  return 0;
}
