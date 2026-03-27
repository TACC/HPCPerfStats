#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "amd64_pmc.h"
#undef KEYS
#include "amd64_df.h"
#undef KEYS
#include "likwid_pmc_adapter.h"
#include "likwid_arch_map.h"
#include "likwid_metrics.h"

#define IA32_CTR0 0xC1
#define IA32_CTR1 0xC2
#define IA32_CTR2 0xC3
#define IA32_CTR3 0xC4
#define IA32_FIXED_CTR0 0x309
#define IA32_FIXED_CTR1 0x30A
#define IA32_FIXED_CTR2 0x30B

static int g_likwid_ready = 0;

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

static int likwid_metrics_begin(struct stats_type *type)
{
  if (likwid_pmc_adapter_init(nr_cpus) == 0 &&
      likwid_pmc_adapter_setup_events(likwid_arch_eventset()) == 0) {
    g_likwid_ready = 1;
    return 0;
  }
  g_likwid_ready = 0;
  type->st_enabled = 1;
  return 0;
}

static void likwid_metrics_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    struct stats *stats;
    snprintf(cpu, sizeof(cpu), "%d", i);
    stats = get_current_stats(type, cpu);
    if (stats == NULL)
      continue;
    if (g_likwid_ready) {
      uint64_t ctls[8] = {0};
      if (likwid_pmc_adapter_read_cpu(stats, i, ctls, 8, 8) == 0)
        continue;
    }
    fallback_fill(stats, cpu);
  }
}

struct stats_type likwid_metrics_stats_type = {
  .st_name = "likwid_metrics",
  .st_begin = &likwid_metrics_begin,
  .st_collect = &likwid_metrics_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(LIKWID_METRICS_KEYS),
#undef X
};
