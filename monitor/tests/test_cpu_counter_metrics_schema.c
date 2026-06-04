#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpu_counter_metrics.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char cpu_counter_schema_def[] = JOIN(CPU_COUNTER_METRICS_KEYS);
#undef X

int main(void)
{
  assert(strstr(cpu_counter_schema_def, " dcgm_cpu_power_util_w,U=W") != NULL);
  assert(strstr(cpu_counter_schema_def, " dcgm_cpu_power_limit_w,U=W") != NULL);
  printf("test_cpu_counter_metrics_schema passed\n");
  return 0;
}
