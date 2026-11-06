#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpu_counter_metrics.h"
#include "stats.h"

#define X SCHEMA_DEF
static const char cpu_counter_schema_def[] = JOIN(CPU_COUNTER_METRICS_KEYS);
#undef X

static void assert_forbidden_legacy_keys_absent(const char *schema)
{
  assert(strstr(schema, " FIXED_CTR") == NULL);
  assert(strstr(schema, " CTL0") == NULL);
  assert(strstr(schema, " MSR_") == NULL);
}

int main(void)
{
  assert(strcmp(CPU_COUNTER_METRICS_ST_NAME, "host_cpu_hw") == 0);
  assert(strstr(cpu_counter_schema_def, " instr_retired,") != NULL);
  assert(strstr(cpu_counter_schema_def, " aperf,") != NULL);
  /* Retired in round 2 (D33/D36): socket power is the per-socket host_power_socket type. */
  assert(strstr(cpu_counter_schema_def, "dcgm_cpu_power") == NULL);
  assert(strstr(cpu_counter_schema_def, " arm_int8_ops,") != NULL);
  assert(strstr(cpu_counter_schema_def, " arm_int16_ops,") != NULL);
  assert_forbidden_legacy_keys_absent(cpu_counter_schema_def);
  printf("test_cpu_counter_metrics_schema passed\n");
  return 0;
}
