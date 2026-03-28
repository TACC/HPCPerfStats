#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#ifdef MONITOR_CPU_BACKEND_DCGM
#include "dcgm_agent.h"
#include "dcgm_structs.h"
#else
#include "amd64_pmc.h"
#undef KEYS
#include "amd64_df.h"
#undef KEYS
#include "likwid_pmc_adapter.h"
#include "likwid_arch_map.h"
#endif
#include "cpu_counter_metrics.h"

#define IA32_CTR0 0xC1
#define IA32_CTR1 0xC2
#define IA32_CTR2 0xC3
#define IA32_CTR3 0xC4
#define IA32_FIXED_CTR0 0x309
#define IA32_FIXED_CTR1 0x30A
#define IA32_FIXED_CTR2 0x30B

#ifdef MONITOR_CPU_BACKEND_DCGM
static int g_dcgm_ready = 0;
static dcgmHandle_t g_dcgm_handle = (dcgmHandle_t) NULL;
static unsigned long long *g_dcgm_ctr0 = NULL;
static unsigned long long *g_dcgm_ctr1 = NULL;
static unsigned long long *g_dcgm_ctr2 = NULL;
static unsigned long long *g_dcgm_ctr3 = NULL;
static unsigned long long *g_dcgm_ctr4 = NULL;
static unsigned long long *g_dcgm_ctr5 = NULL;
static unsigned long long *g_dcgm_inst = NULL;
static unsigned long long *g_dcgm_aperf = NULL;
static unsigned long long *g_dcgm_mperf = NULL;
static long long *g_dcgm_last_ts = NULL;
static long long *g_dcgm_wall_last_us = NULL;

struct dcgm_cpu_sample {
  double util_total;
  double util_user;
  double util_nice;
  double util_sys;
  double util_irq;
  double clock_khz;
  long long ts;
};

static double clamp_percent(double v)
{
  if (v < 0.0)
    return 0.0;
  if (v > 100.0)
    return 100.0;
  return v;
}

static int read_dcgm_cpu_sample(int core_id, struct dcgm_cpu_sample *s)
{
#ifndef DCGM_FI_DEV_CPU_UTIL_TOTAL
#define DCGM_FI_DEV_CPU_UTIL_TOTAL 1100
#endif
#ifndef DCGM_FI_DEV_CPU_UTIL_USER
#define DCGM_FI_DEV_CPU_UTIL_USER 1101
#endif
#ifndef DCGM_FI_DEV_CPU_UTIL_NICE
#define DCGM_FI_DEV_CPU_UTIL_NICE 1102
#endif
#ifndef DCGM_FI_DEV_CPU_UTIL_SYS
#define DCGM_FI_DEV_CPU_UTIL_SYS 1103
#endif
#ifndef DCGM_FI_DEV_CPU_UTIL_IRQ
#define DCGM_FI_DEV_CPU_UTIL_IRQ 1104
#endif
#ifndef DCGM_FI_DEV_CPU_CLOCK_CURRENT
#define DCGM_FI_DEV_CPU_CLOCK_CURRENT 1120
#endif
  static const unsigned short field_ids[] = {
    DCGM_FI_DEV_CPU_UTIL_TOTAL,
    DCGM_FI_DEV_CPU_UTIL_USER,
    DCGM_FI_DEV_CPU_UTIL_NICE,
    DCGM_FI_DEV_CPU_UTIL_SYS,
    DCGM_FI_DEV_CPU_UTIL_IRQ,
    DCGM_FI_DEV_CPU_CLOCK_CURRENT
  };
  dcgmFieldValue_v1 values[sizeof(field_ids) / sizeof(field_ids[0])];
  unsigned int f;
  int ok_util = 0;
  int ok_clock = 0;
  memset(s, 0, sizeof(*s));
  memset(values, 0, sizeof(values));
  /* dcgmEntityGetLatestValues is the stable name in dcgm_agent.h; some stacks only
   * expose that (not dcgmGetLatestValuesForEntity). DCGM_FE_*_CORE from dcgm_fields.h. */
  if (dcgmEntityGetLatestValues(g_dcgm_handle,
                                DCGM_FE_CPU_CORE,
                                core_id,
                                (unsigned short *) field_ids,
                                (unsigned int) (sizeof(field_ids) / sizeof(field_ids[0])),
                                values) != DCGM_ST_OK)
    return -1;

  for (f = 0; f < (unsigned int) (sizeof(field_ids) / sizeof(field_ids[0])); f++) {
    double v;

    if (values[f].status != DCGM_ST_OK)
      continue;
    v = (values[f].fieldType == DCGM_FT_DOUBLE) ? values[f].value.dbl : (double) values[f].value.i64;
    if (values[f].ts > s->ts)
      s->ts = values[f].ts;
    switch (field_ids[f]) {
      case DCGM_FI_DEV_CPU_UTIL_TOTAL:
        s->util_total = clamp_percent(v);
        ok_util = 1;
        break;
      case DCGM_FI_DEV_CPU_UTIL_USER: s->util_user = clamp_percent(v); break;
      case DCGM_FI_DEV_CPU_UTIL_NICE: s->util_nice = clamp_percent(v); break;
      case DCGM_FI_DEV_CPU_UTIL_SYS: s->util_sys = clamp_percent(v); break;
      case DCGM_FI_DEV_CPU_UTIL_IRQ: s->util_irq = clamp_percent(v); break;
      case DCGM_FI_DEV_CPU_CLOCK_CURRENT:
        s->clock_khz = (v < 0.0) ? 0.0 : v;
        ok_clock = 1;
        break;
      default: break;
    }
  }
  if (!ok_util || !ok_clock) {
    memset(s, 0, sizeof(*s));
    return -1;
  }
  return 0;
}

static void publish_dcgm_cpu_stats(struct stats *stats, int i)
{
  stats_set(stats, "CTR0", g_dcgm_ctr0[i]);
  stats_set(stats, "CTR1", g_dcgm_ctr1[i]);
  stats_set(stats, "CTR2", g_dcgm_ctr2[i]);
  stats_set(stats, "CTR3", g_dcgm_ctr3[i]);
  stats_set(stats, "CTR4", g_dcgm_ctr4[i]);
  stats_set(stats, "CTR5", g_dcgm_ctr5[i]);
  stats_set(stats, "CTR6", 0);
  stats_set(stats, "CTR7", 0);
  stats_set(stats, "FIXED_CTR0", 0);
  stats_set(stats, "FIXED_CTR1", 0);
  stats_set(stats, "FIXED_CTR2", 0);
  stats_set(stats, "INST_RETIRED", g_dcgm_inst[i]);
  stats_set(stats, "APERF", g_dcgm_aperf[i]);
  stats_set(stats, "MPERF", g_dcgm_mperf[i]);
  stats_set(stats, "DF_CTR0", 0);
  stats_set(stats, "DF_CTR1", 0);
  stats_set(stats, "DF_CTR2", 0);
  stats_set(stats, "DF_CTR3", 0);
}

static void dcgm_accumulate_from_util_sample(int i, struct dcgm_cpu_sample *sample,
					     long long delta_us)
{
  if (delta_us <= 0 || sample->clock_khz <= 0.0)
    return;
  double ref_cycles = (sample->clock_khz * (double) delta_us) / 1000.0;
  double act_cycles = ref_cycles * (sample->util_total / 100.0);
  g_dcgm_mperf[i] += (unsigned long long) (ref_cycles + 0.5);
  g_dcgm_aperf[i] += (unsigned long long) (act_cycles + 0.5);
  g_dcgm_inst[i] += (unsigned long long) ((ref_cycles * (sample->util_user / 100.0)) + 0.5);
  g_dcgm_ctr0[i] += (unsigned long long) ((sample->util_total * (double) delta_us) + 0.5);
  g_dcgm_ctr1[i] += (unsigned long long) ((sample->util_user * (double) delta_us) + 0.5);
  g_dcgm_ctr2[i] += (unsigned long long) ((sample->util_sys * (double) delta_us) + 0.5);
  g_dcgm_ctr3[i] += (unsigned long long) ((sample->util_irq * (double) delta_us) + 0.5);
  g_dcgm_ctr4[i] += (unsigned long long) ((sample->util_nice * (double) delta_us) + 0.5);
  g_dcgm_ctr5[i] += (unsigned long long) ((sample->clock_khz * (double) delta_us) / 1000.0 + 0.5);
}

static int dcgm_backend_begin(struct stats_type *type)
{
  size_t n = (size_t) nr_cpus;
  dcgmReturn_t rc = dcgmInit();
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM CPU backend init failed\n");
    type->st_enabled = 0;
    return 0;
  }
  rc = dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, &g_dcgm_handle);
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM CPU backend embedded mode failed\n");
    (void) dcgmShutdown();
    type->st_enabled = 0;
    return 0;
  }
  g_dcgm_ctr0 = (unsigned long long *) calloc(n, sizeof(*g_dcgm_ctr0));
  g_dcgm_ctr1 = (unsigned long long *) calloc(n, sizeof(*g_dcgm_ctr1));
  g_dcgm_ctr2 = (unsigned long long *) calloc(n, sizeof(*g_dcgm_ctr2));
  g_dcgm_ctr3 = (unsigned long long *) calloc(n, sizeof(*g_dcgm_ctr3));
  g_dcgm_ctr4 = (unsigned long long *) calloc(n, sizeof(*g_dcgm_ctr4));
  g_dcgm_ctr5 = (unsigned long long *) calloc(n, sizeof(*g_dcgm_ctr5));
  g_dcgm_inst = (unsigned long long *) calloc(n, sizeof(*g_dcgm_inst));
  g_dcgm_aperf = (unsigned long long *) calloc(n, sizeof(*g_dcgm_aperf));
  g_dcgm_mperf = (unsigned long long *) calloc(n, sizeof(*g_dcgm_mperf));
  g_dcgm_last_ts = (long long *) calloc(n, sizeof(*g_dcgm_last_ts));
  g_dcgm_wall_last_us = (long long *) calloc(n, sizeof(*g_dcgm_wall_last_us));
  if (g_dcgm_ctr0 == NULL || g_dcgm_ctr1 == NULL || g_dcgm_ctr2 == NULL ||
      g_dcgm_ctr3 == NULL || g_dcgm_ctr4 == NULL || g_dcgm_ctr5 == NULL ||
      g_dcgm_inst == NULL || g_dcgm_aperf == NULL || g_dcgm_mperf == NULL ||
      g_dcgm_last_ts == NULL || g_dcgm_wall_last_us == NULL) {
    ERROR("DCGM CPU backend allocation failed\n");
    type->st_enabled = 0;
    return 0;
  }
  g_dcgm_ready = 1;
  return 0;
}
#else
static int g_likwid_ready = 0;

static int likwid_backend_begin(struct stats_type *type)
{
  (void)type;
  if (likwid_pmc_adapter_init(nr_cpus) == 0 &&
      likwid_pmc_adapter_setup_events(likwid_arch_eventset()) == 0) {
    g_likwid_ready = 1;
    return 0;
  }
  g_likwid_ready = 0;
  type->st_enabled = 1;
  return 0;
}
#endif

#ifndef MONITOR_CPU_BACKEND_DCGM
static int read_msr_u64(const char *cpu, uint64_t reg, uint64_t *val)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0)
    return -1;
  if (pread(msr_fd, val, sizeof(*val), reg) == (ssize_t) sizeof(*val))
    rc = 0;
  close(msr_fd);
  return rc;
}

static void fallback_fill(struct stats *stats, const char *cpu)
{
  uint64_t v = 0;
#ifdef MONITOR_ARCH_INTEL
  if (read_msr_u64(cpu, IA32_CTR0, &v) == 0) stats_set(stats, "CTR0", v);
  if (read_msr_u64(cpu, IA32_CTR1, &v) == 0) stats_set(stats, "CTR1", v);
  if (read_msr_u64(cpu, IA32_CTR2, &v) == 0) stats_set(stats, "CTR2", v);
  if (read_msr_u64(cpu, IA32_CTR3, &v) == 0) stats_set(stats, "CTR3", v);
  if (read_msr_u64(cpu, IA32_FIXED_CTR0, &v) == 0) stats_set(stats, "FIXED_CTR0", v);
  if (read_msr_u64(cpu, IA32_FIXED_CTR1, &v) == 0) stats_set(stats, "FIXED_CTR1", v);
  if (read_msr_u64(cpu, IA32_FIXED_CTR2, &v) == 0) stats_set(stats, "FIXED_CTR2", v);
#else
  if (read_msr_u64(cpu, MSR_PERF_CTR0, &v) == 0) stats_set(stats, "CTR0", v);
  if (read_msr_u64(cpu, MSR_PERF_CTR1, &v) == 0) stats_set(stats, "CTR1", v);
  if (read_msr_u64(cpu, MSR_PERF_CTR2, &v) == 0) stats_set(stats, "CTR2", v);
  if (read_msr_u64(cpu, MSR_PERF_CTR3, &v) == 0) stats_set(stats, "CTR3", v);
  if (read_msr_u64(cpu, MSR_PERF_CTR4, &v) == 0) stats_set(stats, "CTR4", v);
  if (read_msr_u64(cpu, MSR_PERF_CTR5, &v) == 0) stats_set(stats, "CTR5", v);
  if (read_msr_u64(cpu, MSR_PERF_INST_RETIRED, &v) == 0) stats_set(stats, "INST_RETIRED", v);
  if (read_msr_u64(cpu, MSR_PERF_APERF, &v) == 0) stats_set(stats, "APERF", v);
  if (read_msr_u64(cpu, MSR_PERF_MPERF, &v) == 0) stats_set(stats, "MPERF", v);
  if (read_msr_u64(cpu, MSR_DF_CTR0, &v) == 0) stats_set(stats, "DF_CTR0", v);
  if (read_msr_u64(cpu, MSR_DF_CTR1, &v) == 0) stats_set(stats, "DF_CTR1", v);
  if (read_msr_u64(cpu, MSR_DF_CTR2, &v) == 0) stats_set(stats, "DF_CTR2", v);
  if (read_msr_u64(cpu, MSR_DF_CTR3, &v) == 0) stats_set(stats, "DF_CTR3", v);
#endif
}
#endif

static int cpu_counter_metrics_begin(struct stats_type *type)
{
#ifdef MONITOR_CPU_BACKEND_DCGM
  return dcgm_backend_begin(type);
#else
  return likwid_backend_begin(type);
#endif
}

static void cpu_counter_metrics_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    struct stats *stats;
    snprintf(cpu, sizeof(cpu), "%d", i);
    stats = get_current_stats(type, cpu);
    if (stats == NULL)
      continue;
    if (
#ifdef MONITOR_CPU_BACKEND_DCGM
        g_dcgm_ready
#else
        g_likwid_ready
#endif
    ) {
#ifdef MONITOR_CPU_BACKEND_DCGM
      struct dcgm_cpu_sample sample;
      long long delta_us = 0;
      struct timespec mono;
      long long mono_us = 0;

      memset(&sample, 0, sizeof(sample));
      if (clock_gettime(CLOCK_MONOTONIC, &mono) == 0)
        mono_us = (long long) mono.tv_sec * 1000000LL + (long long) mono.tv_nsec / 1000LL;
      if (read_dcgm_cpu_sample(i, &sample) == 0) {
        /* Prefer DCGM field timestamps; many ARM/embedded stacks leave ts at 0. */
        if (sample.ts > 0) {
          if (g_dcgm_last_ts[i] > 0 && sample.ts > g_dcgm_last_ts[i])
            delta_us = sample.ts - g_dcgm_last_ts[i];
          g_dcgm_last_ts[i] = sample.ts;
        } else if (mono_us > 0) {
          if (g_dcgm_wall_last_us[i] > 0 && mono_us > g_dcgm_wall_last_us[i])
            delta_us = mono_us - g_dcgm_wall_last_us[i];
          g_dcgm_wall_last_us[i] = mono_us;
        }
      }
      dcgm_accumulate_from_util_sample(i, &sample, delta_us);
      publish_dcgm_cpu_stats(stats, i);
      continue;
#else
      uint64_t ctls[8] = {0};
      if (likwid_pmc_adapter_read_cpu(stats, i, ctls, 8, 8) == 0)
        continue;
#endif
    }
#ifndef MONITOR_CPU_BACKEND_DCGM
    fallback_fill(stats, cpu);
#endif
  }
}

struct stats_type cpu_counter_metrics_stats_type = {
  .st_begin = &cpu_counter_metrics_begin,
  .st_collect = &cpu_counter_metrics_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(CPU_COUNTER_METRICS_KEYS),
#undef X
  .st_name = "cpu_counter_metrics",
};
