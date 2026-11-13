/* host_cpu_hw schema contract. Built twice: the configured backend (x86: likwid) and
 * test_cpu_counter_metrics_schema_arm (-DMONITOR_CPU_BACKEND_ARM, the Grace schema). */
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

/* O11: shared hardware-width keys stay W=48 (the arm reader emits them modulo 2^48). */
static void assert_shared_key_widths(const char *schema)
{
  assert(strstr(schema, " instr_retired,E,W=48") != NULL);
  assert(strstr(schema, " aperf,E,W=48") != NULL);
  assert(strstr(schema, " mperf,E,W=48") != NULL);
  assert(strstr(schema, " arm_int8_ops,E,W=48") != NULL);
  assert(strstr(schema, " arm_int16_ops,E,W=48") != NULL);
  assert(strstr(schema, " cpu_clock_est_cycles,E,W=64") != NULL);
  assert(strstr(schema, " arm_est_flops,E,W=64,U=FLOP") != NULL);
}

/* D1: Grace measured FP instruction keys are W=64 software lifetimes, on arm builds only
 * (the x86 host_cpu_hw schema is unchanged). */
static void assert_d1_keys(const char *schema)
{
#ifdef MONITOR_CPU_BACKEND_ARM
  assert(strstr(schema, " arm_fp_dp_spec,E,W=64") != NULL);
  assert(strstr(schema, " arm_fp_sp_spec,E,W=64") != NULL);
  assert(strstr(schema, " arm_fp_hp_spec,E,W=64") != NULL);
#else
  assert(strstr(schema, "arm_fp_dp_spec") == NULL);
  assert(strstr(schema, "arm_fp_sp_spec") == NULL);
  assert(strstr(schema, "arm_fp_hp_spec") == NULL);
#endif
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
  assert_shared_key_widths(cpu_counter_schema_def);
  assert_d1_keys(cpu_counter_schema_def);
  printf("test_cpu_counter_metrics_schema passed\n");
  return 0;
}
