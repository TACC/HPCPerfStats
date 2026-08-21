#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "monitor_log.h"
#ifdef MONITOR_CPU_BACKEND_DCGM
#include "dcgm_agent.h"
#include "dcgm_fields.h"
#include "dcgm_structs.h"
#include "dcgm_session.h"
#include "cpu_counter_metrics_dcgm_state.h"
#include "cpu_counter_metrics_dcgm_publish.h"
#include "cpu_counter_metrics_dcgm_util.h"
#ifdef MONITOR_CPU_PAPI_FLOPS
#include "cpu_counter_metrics_papi.h"
#endif
#else
#include "likwid_pmc_adapter.h"
#include "likwid_arch_map.h"
#include "cpu_counter_metrics_likwid_begin.h"
#endif
#include "cpu_counter_metrics.h"

#ifdef MONITOR_CPU_BACKEND_DCGM
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
#ifndef DCGM_FI_DEV_CPU_POWER_UTIL_CURRENT
#define DCGM_FI_DEV_CPU_POWER_UTIL_CURRENT 1130
#endif
#ifndef DCGM_FI_DEV_CPU_POWER_LIMIT
#define DCGM_FI_DEV_CPU_POWER_LIMIT 1131
#endif

static int g_dcgm_ready = 0;
static unsigned long g_dcgm_update_failures;
static time_t g_dcgm_retry_after;
static dcgmHandle_t g_dcgm_handle = (dcgmHandle_t)NULL;
static int g_dcgm_cpu_use_disconnect = 0;
static int g_dcgm_cpu_session_held;
static dcgmGpuGrp_t *g_dcgm_cpu_groups = NULL;
static dcgmFieldGrp_t *g_dcgm_cpu_fgs = NULL;
static int g_dcgm_cpu_nchunks = 0;
static int g_dcgm_watch_active = 0;
static struct dcgm_cpu_sample *g_dcgm_sample_cache = NULL;
static unsigned char *g_dcgm_sample_valid = NULL;
unsigned long long *g_dcgm_ctr0 = NULL;
unsigned long long *g_dcgm_ctr1 = NULL;
unsigned long long *g_dcgm_ctr2 = NULL;
unsigned long long *g_dcgm_ctr3 = NULL;
unsigned long long *g_dcgm_ctr4 = NULL;
unsigned long long *g_dcgm_ctr5 = NULL;
unsigned long long *g_dcgm_inst = NULL;
unsigned long long *g_dcgm_aperf = NULL;
unsigned long long *g_dcgm_mperf = NULL;
unsigned long long *g_dcgm_arm_est_flops = NULL;
unsigned long long *g_dcgm_arm_dram_bytes = NULL;
unsigned long long *g_dcgm_fp_sca_d = NULL;
unsigned long long *g_dcgm_fp_128_d = NULL;
unsigned long long *g_dcgm_fp_256_d = NULL;
unsigned long long *g_dcgm_fp_512_d = NULL;
unsigned long long *g_dcgm_fp_sca_s = NULL;
unsigned long long *g_dcgm_fp_128_s = NULL;
unsigned long long *g_dcgm_fp_256_s = NULL;
unsigned long long *g_dcgm_fp_512_s = NULL;
static long long *g_dcgm_last_ts = NULL;
static long long g_dcgm_mono_prev_us = 0;

/* DCGM_FE_CPU (socket) power: fields 1130/1131; mapped to logical CPUs via sysfs packages. */
static dcgmGpuGrp_t g_dcgm_sock_group = (dcgmGpuGrp_t)NULL;
static dcgmFieldGrp_t g_dcgm_sock_fg = (dcgmFieldGrp_t)NULL;
static int *g_dcgm_cpu_entity_list = NULL;
int g_dcgm_ncpu_entities = 0;
int *g_dcgm_logical_to_power_slot = NULL;
double *g_dcgm_sock_power_util = NULL;
double *g_dcgm_sock_power_limit = NULL;
static int g_dcgm_sock_map_mismatch_logged = 0;
static const unsigned short g_dcgm_cpu_power_field_ids[] = {DCGM_FI_DEV_CPU_POWER_UTIL_CURRENT,
                                                            DCGM_FI_DEV_CPU_POWER_LIMIT};
#define DCGM_CPU_POWER_NFIELDS                                                                     \
  ((unsigned int)(sizeof(g_dcgm_cpu_power_field_ids) / sizeof(g_dcgm_cpu_power_field_ids[0])))

/* Approximation knobs for ARM/DCGM-derived roofline metrics.
 * DCGM CPU does not expose direct DRAM-channel bytes or architectural FP
 * counters; we synthesize monotonic host-level signals from active cycles.
 */
#define ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE 2.0
#define ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE 16.0
/* Synthesize width-resolved FP_ARITH-style counters for ARM/DCGM so
 * vecpercent/avg_vector_width metrics can be computed in analysis.
 */
#define ARM_APPROX_FP64_FLOP_SHARE 0.5
#define ARM_APPROX_FP64_VECTOR_FLOP_SHARE 0.6
#define ARM_APPROX_FP32_VECTOR_FLOP_SHARE 0.8

static struct dcgm_cpu_jifs *g_dcjm_prev = NULL;
static struct dcgm_cpu_jifs *g_dcjm_cur = NULL;
static int g_dcgm_stat_seeded = 0;
static FILE *g_dcgm_proc_stat = NULL;

static const unsigned short g_dcgm_cpu_field_ids[] = {
    DCGM_FI_DEV_CPU_UTIL_TOTAL, DCGM_FI_DEV_CPU_UTIL_USER, DCGM_FI_DEV_CPU_UTIL_NICE,
    DCGM_FI_DEV_CPU_UTIL_SYS,   DCGM_FI_DEV_CPU_UTIL_IRQ,  DCGM_FI_DEV_CPU_CLOCK_CURRENT};

#define DCGM_CPU_NFIELDS                                                                           \
  ((unsigned int)(sizeof(g_dcgm_cpu_field_ids) / sizeof(g_dcgm_cpu_field_ids[0])))

static double dcgm_cpu_read_khz_sysfs_path(const char *path)
{
  char buf[40];
  int fd;
  ssize_t n;
  long long khz = 0;

  fd = open(path, O_RDONLY);
  if (fd < 0)
    return 0.0;
  n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0)
    return 0.0;
  buf[n] = '\0';
  if (sscanf(buf, "%lld", &khz) != 1 || khz <= 0)
    return 0.0;
  return (double)khz;
}

static double dcgm_cpu_nominal_freq_khz(int core_id)
{
  char path[160];
  double khz;

  snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/base_frequency", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_max_freq", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  if (core_id != 0)
    return dcgm_cpu_nominal_freq_khz(0);
  return 0.0;
}

static int dcgm_proc_stat_read_cpus(struct dcgm_cpu_jifs *out, int ncpus)
{
  char *line = NULL;
  size_t line_size = 0;
  int any = 0;

  if (ncpus <= 0 || out == NULL)
    return -1;
  memset(out, 0, (size_t)ncpus * sizeof(*out));
  if (g_dcgm_proc_stat == NULL) {
    g_dcgm_proc_stat = fopen("/proc/stat", "re");
    if (g_dcgm_proc_stat == NULL)
      return -1;
  }
  rewind(g_dcgm_proc_stat);
  clearerr(g_dcgm_proc_stat);
  while (getline(&line, &line_size, g_dcgm_proc_stat) >= 0) {
    char *p = line;
    int idcpu = -1;

    if (strncmp(p, "cpu", 3) != 0)
      continue;
    p += 3;
    if (!isdigit((unsigned char)*p))
      continue;
    idcpu = (int)strtol(p, &p, 10);
    if (idcpu < 0 || idcpu >= ncpus)
      continue;
    while (*p == ' ' || *p == '\t')
      p++;
    {
      struct dcgm_cpu_jifs j;
      int nf;

      memset(&j, 0, sizeof(j));
      nf = sscanf(p, "%llu %llu %llu %llu %llu %llu %llu %llu %llu %llu", &j.u, &j.nice, &j.sys,
                  &j.idle, &j.iow, &j.irq, &j.sft, &j.stl, &j.gu, &j.gn);
      if (nf < 4)
        continue;
      out[idcpu] = j;
      any = 1;
    }
  }
  free(line);
  return any ? 0 : -1;
}

static int dcgm_cpu_fill_sample_from_v1(unsigned int nfields, const unsigned short *field_ids,
                                        const dcgmFieldValue_v1 *values, struct dcgm_cpu_sample *s)
{
  unsigned int f;
  int ok_util = 0;

  memset(s, 0, sizeof(*s));
  for (f = 0; f < nfields; f++) {
    double v;

    if (values[f].status != DCGM_ST_OK)
      continue;
    v = (values[f].fieldType == DCGM_FT_DOUBLE) ? values[f].value.dbl : (double)values[f].value.i64;
    if (values[f].ts > s->ts)
      s->ts = values[f].ts;
    switch (field_ids[f]) {
    case DCGM_FI_DEV_CPU_UTIL_TOTAL:
      s->util_total = dcgm_clamp_percent(v);
      ok_util = 1;
      break;
    case DCGM_FI_DEV_CPU_UTIL_USER:
      s->util_user = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_NICE:
      s->util_nice = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_SYS:
      s->util_sys = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_IRQ:
      s->util_irq = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_CLOCK_CURRENT:
      s->clock_khz = (v < 0.0) ? 0.0 : v;
      break;
    default:
      break;
    }
  }
  return ok_util ? 0 : -1;
}

static int dcgm_cpu_fill_sample_from_v1_any(const dcgmFieldValue_v1 *values, int n,
                                            struct dcgm_cpu_sample *s)
{
  int f;
  int ok_util = 0;

  memset(s, 0, sizeof(*s));
  for (f = 0; f < n; f++) {
    double v;

    if (values[f].status != DCGM_ST_OK)
      continue;
    v = (values[f].fieldType == DCGM_FT_DOUBLE) ? values[f].value.dbl : (double)values[f].value.i64;
    if (values[f].ts > s->ts)
      s->ts = values[f].ts;
    switch (values[f].fieldId) {
    case DCGM_FI_DEV_CPU_UTIL_TOTAL:
      s->util_total = dcgm_clamp_percent(v);
      ok_util = 1;
      break;
    case DCGM_FI_DEV_CPU_UTIL_USER:
      s->util_user = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_NICE:
      s->util_nice = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_SYS:
      s->util_sys = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_IRQ:
      s->util_irq = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_CLOCK_CURRENT:
      s->clock_khz = (v < 0.0) ? 0.0 : v;
      break;
    default:
      break;
    }
  }
  return ok_util ? 0 : -1;
}

static int dcgm_cpu_fill_sample_from_v2(const dcgmFieldValue_v2 *values, unsigned int n,
                                        struct dcgm_cpu_sample *s)
{
  unsigned int f;
  int ok_util = 0;

  memset(s, 0, sizeof(*s));
  for (f = 0; f < n; f++) {
    double v;

    if (values[f].status != DCGM_ST_OK)
      continue;
    v = (values[f].fieldType == DCGM_FT_DOUBLE) ? values[f].value.dbl : (double)values[f].value.i64;
    if (values[f].ts > s->ts)
      s->ts = values[f].ts;
    switch (values[f].fieldId) {
    case DCGM_FI_DEV_CPU_UTIL_TOTAL:
      s->util_total = dcgm_clamp_percent(v);
      ok_util = 1;
      break;
    case DCGM_FI_DEV_CPU_UTIL_USER:
      s->util_user = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_NICE:
      s->util_nice = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_SYS:
      s->util_sys = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_UTIL_IRQ:
      s->util_irq = dcgm_clamp_percent(v);
      break;
    case DCGM_FI_DEV_CPU_CLOCK_CURRENT:
      s->clock_khz = (v < 0.0) ? 0.0 : v;
      break;
    default:
      break;
    }
  }
  return ok_util ? 0 : -1;
}

static int read_dcgm_cpu_sample_live(int core_id, struct dcgm_cpu_sample *s)
{
  dcgmGroupEntityPair_t ent;
  dcgmFieldValue_v2 values[DCGM_CPU_NFIELDS];
  unsigned int fi;
  dcgmReturn_t rc;

  ent.entityGroupId = DCGM_FE_CPU_CORE;
  ent.entityId = (dcgm_field_eid_t)core_id;
  for (fi = 0; fi < DCGM_CPU_NFIELDS; fi++)
    values[fi].version = dcgmFieldValue_version2;

  rc = dcgmEntitiesGetLatestValues(g_dcgm_handle, &ent, 1, (unsigned short *)g_dcgm_cpu_field_ids,
                                   DCGM_CPU_NFIELDS, DCGM_FV_FLAG_LIVE_DATA, values);
  if (rc != DCGM_ST_OK)
    return -1;
  return dcgm_cpu_fill_sample_from_v2(values, DCGM_CPU_NFIELDS, s);
}

static int read_dcgm_cpu_sample(int core_id, struct dcgm_cpu_sample *s)
{
  dcgmFieldValue_v1 values[DCGM_CPU_NFIELDS];

  memset(values, 0, sizeof(values));
  if (dcgmEntityGetLatestValues(g_dcgm_handle, DCGM_FE_CPU_CORE, core_id,
                                (unsigned short *)g_dcgm_cpu_field_ids, DCGM_CPU_NFIELDS,
                                values) == DCGM_ST_OK &&
      dcgm_cpu_fill_sample_from_v1(DCGM_CPU_NFIELDS, g_dcgm_cpu_field_ids, values, s) == 0) {
    if (s->clock_khz <= 0.0)
      s->clock_khz = dcgm_cpu_nominal_freq_khz(core_id);
    return 0;
  }
  if (read_dcgm_cpu_sample_live(core_id, s) != 0) {
    memset(s, 0, sizeof(*s));
    return -1;
  }
  if (s->clock_khz <= 0.0)
    s->clock_khz = dcgm_cpu_nominal_freq_khz(core_id);
  return 0;
}

static int dcgm_cpu_cache_list_values(unsigned int entity_id, dcgmFieldValue_v1 *values,
                                      int num_values, void *userdata)
{
  struct dcgm_cpu_sample *cache = (struct dcgm_cpu_sample *)userdata;
  struct dcgm_cpu_sample sample;

  if (cache == NULL || entity_id >= (unsigned int)nr_cpus || values == NULL || num_values <= 0)
    return -1;
  if (dcgm_cpu_fill_sample_from_v1_any(values, num_values, &sample) != 0)
    return 0;
  if (sample.clock_khz <= 0.0)
    sample.clock_khz = dcgm_cpu_nominal_freq_khz((int)entity_id);
  cache[entity_id] = sample;
  g_dcgm_sample_valid[entity_id] = 1;
  return 0;
}

static void dcgm_cpu_refresh_sample_cache(void)
{
  int c;

  if (!g_dcgm_watch_active || g_dcgm_handle == (dcgmHandle_t)NULL || g_dcgm_cpu_groups == NULL ||
      g_dcgm_cpu_fgs == NULL || g_dcgm_sample_cache == NULL || g_dcgm_sample_valid == NULL ||
      nr_cpus <= 0)
    return;

  memset(g_dcgm_sample_valid, 0, (size_t)nr_cpus * sizeof(*g_dcgm_sample_valid));
  for (c = 0; c < g_dcgm_cpu_nchunks; c++) {
    if (g_dcgm_cpu_groups[c] == (dcgmGpuGrp_t)NULL || g_dcgm_cpu_fgs[c] == (dcgmFieldGrp_t)NULL)
      continue;
    (void)dcgmGetLatestValues(g_dcgm_handle, g_dcgm_cpu_groups[c], g_dcgm_cpu_fgs[c],
                              &dcgm_cpu_cache_list_values, g_dcgm_sample_cache);
  }
}

static int dcgm_cmp_int(const void *a, const void *b)
{
  int x = *(const int *)a;
  int y = *(const int *)b;

  if (x < y)
    return -1;
  if (x > y)
    return 1;
  return 0;
}

static int dcgm_sysfs_physical_package_id(int cpu_idx, int *pkg_out)
{
  char path[120];
  char buf[32];
  int fd;
  ssize_t n;
  long v;

  if (pkg_out == NULL)
    return -1;
  snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/topology/physical_package_id",
           cpu_idx);
  fd = open(path, O_RDONLY);
  if (fd < 0)
    return -1;
  n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0)
    return -1;
  buf[n] = '\0';
  if (sscanf(buf, "%ld", &v) != 1)
    return -1;
  *pkg_out = (int)v;
  return 0;
}

static void dcgm_cpu_sock_watch_cleanup(void)
{
  if (g_dcgm_handle != (dcgmHandle_t)NULL) {
    if (g_dcgm_sock_fg != (dcgmFieldGrp_t)NULL)
      (void)dcgmFieldGroupDestroy(g_dcgm_handle, g_dcgm_sock_fg);
    if (g_dcgm_sock_group != (dcgmGpuGrp_t)NULL)
      (void)dcgmGroupDestroy(g_dcgm_handle, g_dcgm_sock_group);
  }
  g_dcgm_sock_fg = (dcgmFieldGrp_t)NULL;
  g_dcgm_sock_group = (dcgmGpuGrp_t)NULL;
  free(g_dcgm_cpu_entity_list);
  g_dcgm_cpu_entity_list = NULL;
  g_dcgm_ncpu_entities = 0;
  free(g_dcgm_logical_to_power_slot);
  g_dcgm_logical_to_power_slot = NULL;
  free(g_dcgm_sock_power_util);
  g_dcgm_sock_power_util = NULL;
  free(g_dcgm_sock_power_limit);
  g_dcgm_sock_power_limit = NULL;
  g_dcgm_sock_map_mismatch_logged = 0;
}

/*
 * Pair Linux physical_package_id values (sorted unique) with sorted DCGM_FE_CPU
 * entity ids when counts match; otherwise disable per-socket power for this session.
 */
static int dcgm_topology_build_sock_power_map(void)
{
  int *pkg_per_cpu = NULL;
  int *sorted_pkgs = NULL;
  int *unique_pkg = NULL;
  dcgm_field_eid_t *ent_buf = NULL;
  int n_ent = 0;
  int i, j, nu;
  dcgmReturn_t rc;

  dcgm_cpu_sock_watch_cleanup();
  if (nr_cpus <= 0 || g_dcgm_handle == (dcgmHandle_t)NULL)
    return -1;

  pkg_per_cpu = (int *)calloc((size_t)nr_cpus, sizeof(*pkg_per_cpu));
  sorted_pkgs = (int *)calloc((size_t)nr_cpus, sizeof(*sorted_pkgs));
  unique_pkg = (int *)calloc((size_t)nr_cpus, sizeof(*unique_pkg));
  g_dcgm_logical_to_power_slot =
      (int *)calloc((size_t)nr_cpus, sizeof(*g_dcgm_logical_to_power_slot));
  if (pkg_per_cpu == NULL || sorted_pkgs == NULL || unique_pkg == NULL ||
      g_dcgm_logical_to_power_slot == NULL) {
    free(pkg_per_cpu);
    free(sorted_pkgs);
    free(unique_pkg);
    free(g_dcgm_logical_to_power_slot);
    g_dcgm_logical_to_power_slot = NULL;
    return -1;
  }

  for (i = 0; i < nr_cpus; i++) {
    if (dcgm_sysfs_physical_package_id(i, &pkg_per_cpu[i]) != 0)
      pkg_per_cpu[i] = 0;
    sorted_pkgs[i] = pkg_per_cpu[i];
  }
  qsort(sorted_pkgs, (size_t)nr_cpus, sizeof(*sorted_pkgs), dcgm_cmp_int);
  nu = 0;
  for (i = 0; i < nr_cpus; i++) {
    if (i == 0 || sorted_pkgs[i] != sorted_pkgs[i - 1])
      unique_pkg[nu++] = sorted_pkgs[i];
  }

  n_ent = 32;
  ent_buf = (dcgm_field_eid_t *)calloc((size_t)n_ent, sizeof(*ent_buf));
  if (ent_buf == NULL)
    goto map_fail;
  rc = dcgmGetEntityGroupEntities(g_dcgm_handle, DCGM_FE_CPU, ent_buf, &n_ent, 0);
  if (rc == DCGM_ST_INSUFFICIENT_SIZE && n_ent > 0) {
    free(ent_buf);
    ent_buf = (dcgm_field_eid_t *)calloc((size_t)n_ent, sizeof(*ent_buf));
    if (ent_buf == NULL)
      goto map_fail;
    rc = dcgmGetEntityGroupEntities(g_dcgm_handle, DCGM_FE_CPU, ent_buf, &n_ent, 0);
  }
  if (rc != DCGM_ST_OK || n_ent <= 0) {
    TRACE("DCGM_FE_CPU enumeration failed (rc=%d n=%d); Grace CPU power fields skipped\n", (int)rc,
          n_ent);
    goto map_fail;
  }

  g_dcgm_cpu_entity_list = (int *)calloc((size_t)n_ent, sizeof(*g_dcgm_cpu_entity_list));
  g_dcgm_sock_power_util = (double *)calloc((size_t)n_ent, sizeof(*g_dcgm_sock_power_util));
  g_dcgm_sock_power_limit = (double *)calloc((size_t)n_ent, sizeof(*g_dcgm_sock_power_limit));
  if (g_dcgm_cpu_entity_list == NULL || g_dcgm_sock_power_util == NULL ||
      g_dcgm_sock_power_limit == NULL)
    goto map_fail;

  for (i = 0; i < n_ent; i++)
    g_dcgm_cpu_entity_list[i] = (int)ent_buf[i];
  free(ent_buf);
  ent_buf = NULL;
  qsort(g_dcgm_cpu_entity_list, (size_t)n_ent, sizeof(*g_dcgm_cpu_entity_list), dcgm_cmp_int);

  if (nu != n_ent) {
    if (!g_dcgm_sock_map_mismatch_logged) {
      TRACE("DCGM CPU power: package count %d != DCGM_FE_CPU count %d; socket power not mapped\n",
            nu, n_ent);
      g_dcgm_sock_map_mismatch_logged = 1;
    }
    for (i = 0; i < nr_cpus; i++)
      g_dcgm_logical_to_power_slot[i] = -1;
  } else {
    for (i = 0; i < nr_cpus; i++) {
      int p = pkg_per_cpu[i];
      int slot = -1;

      for (j = 0; j < nu; j++) {
        if (unique_pkg[j] == p) {
          slot = j;
          break;
        }
      }
      g_dcgm_logical_to_power_slot[i] = slot;
    }
  }

  g_dcgm_ncpu_entities = n_ent;
  free(pkg_per_cpu);
  free(sorted_pkgs);
  free(unique_pkg);
  return 0;

map_fail:
  free(ent_buf);
  free(pkg_per_cpu);
  free(sorted_pkgs);
  free(unique_pkg);
  dcgm_cpu_sock_watch_cleanup();
  return -1;
}

static int dcgm_cpu_sock_watch_install(void)
{
  dcgmReturn_t rc;
  int j;

  if (g_dcgm_handle == (dcgmHandle_t)NULL || g_dcgm_ncpu_entities <= 0 ||
      g_dcgm_cpu_entity_list == NULL)
    return -1;

  rc = dcgmGroupCreate(g_dcgm_handle, DCGM_GROUP_EMPTY, "hpc_cpu_sock", &g_dcgm_sock_group);
  if (rc != DCGM_ST_OK)
    goto sock_fail;
  for (j = 0; j < g_dcgm_ncpu_entities; j++) {
    rc = dcgmGroupAddEntity(g_dcgm_handle, g_dcgm_sock_group, DCGM_FE_CPU,
                            (dcgm_field_eid_t)g_dcgm_cpu_entity_list[j]);
    if (rc != DCGM_ST_OK)
      goto sock_fail;
  }
  rc = dcgmFieldGroupCreate(g_dcgm_handle, DCGM_CPU_POWER_NFIELDS,
                            (unsigned short *)g_dcgm_cpu_power_field_ids, "hpc_cpu_sock_fg",
                            &g_dcgm_sock_fg);
  if (rc != DCGM_ST_OK)
    goto sock_fail;
  rc = dcgmWatchFields(g_dcgm_handle, g_dcgm_sock_group, g_dcgm_sock_fg, 1000000LL, 3600.0, 3600);
  if (rc != DCGM_ST_OK)
    goto sock_fail;
  return 0;

sock_fail:
  TRACE("DCGM CPU socket power watch failed (rc=%d); using per-entity reads\n", (int)rc);
  if (g_dcgm_sock_fg != (dcgmFieldGrp_t)NULL)
    (void)dcgmFieldGroupDestroy(g_dcgm_handle, g_dcgm_sock_fg);
  if (g_dcgm_sock_group != (dcgmGpuGrp_t)NULL)
    (void)dcgmGroupDestroy(g_dcgm_handle, g_dcgm_sock_group);
  g_dcgm_sock_fg = (dcgmFieldGrp_t)NULL;
  g_dcgm_sock_group = (dcgmGpuGrp_t)NULL;
  return -1;
}

static void dcgm_cpu_refresh_socket_power(void)
{
  int j;

  if (g_dcgm_handle == (dcgmHandle_t)NULL || g_dcgm_ncpu_entities <= 0 ||
      g_dcgm_cpu_entity_list == NULL || g_dcgm_sock_power_util == NULL ||
      g_dcgm_sock_power_limit == NULL)
    return;

  for (j = 0; j < g_dcgm_ncpu_entities; j++) {
    dcgmFieldValue_v1 vals[DCGM_CPU_POWER_NFIELDS];
    int eid = g_dcgm_cpu_entity_list[j];
    double u = 0.0, lim = 0.0;

    memset(vals, 0, sizeof(vals));
    if (dcgmEntityGetLatestValues(g_dcgm_handle, DCGM_FE_CPU, eid,
                                  (unsigned short *)g_dcgm_cpu_power_field_ids,
                                  DCGM_CPU_POWER_NFIELDS, vals) != DCGM_ST_OK) {
      g_dcgm_sock_power_util[j] = 0.0;
      g_dcgm_sock_power_limit[j] = 0.0;
      continue;
    }
    if (vals[0].status == DCGM_ST_OK) {
      if (vals[0].fieldType == DCGM_FT_DOUBLE)
        u = vals[0].value.dbl;
      else
        u = (double)vals[0].value.i64;
    }
    if (vals[1].status == DCGM_ST_OK) {
      if (vals[1].fieldType == DCGM_FT_DOUBLE)
        lim = vals[1].value.dbl;
      else
        lim = (double)vals[1].value.i64;
    }
    if (u < 0.0 || dcgm_fp64_value_is_blank(u))
      u = 0.0;
    if (lim < 0.0 || dcgm_fp64_value_is_blank(lim))
      lim = 0.0;
    g_dcgm_sock_power_util[j] = u;
    g_dcgm_sock_power_limit[j] = lim;
  }
}

static void dcgm_cpu_watch_cleanup(void)
{
  int c;

  dcgm_cpu_sock_watch_cleanup();
  if (g_dcgm_cpu_groups == NULL && g_dcgm_cpu_fgs == NULL)
    return;
  if (g_dcgm_handle != (dcgmHandle_t)NULL && g_dcgm_cpu_nchunks > 0 && g_dcgm_cpu_groups != NULL &&
      g_dcgm_cpu_fgs != NULL) {
    for (c = 0; c < g_dcgm_cpu_nchunks; c++) {
      if (g_dcgm_cpu_fgs[c] != (dcgmFieldGrp_t)NULL)
        (void)dcgmFieldGroupDestroy(g_dcgm_handle, g_dcgm_cpu_fgs[c]);
      if (g_dcgm_cpu_groups[c] != (dcgmGpuGrp_t)NULL)
        (void)dcgmGroupDestroy(g_dcgm_handle, g_dcgm_cpu_groups[c]);
    }
  }
  free(g_dcgm_cpu_groups);
  free(g_dcgm_cpu_fgs);
  g_dcgm_cpu_groups = NULL;
  g_dcgm_cpu_fgs = NULL;
  g_dcgm_cpu_nchunks = 0;
  g_dcgm_watch_active = 0;
}

static void dcgm_backend_teardown_session(void)
{
  /* Drop DCGM watches/handle only — keep util accumulators, jiffy bufs, and PAPI. */
  dcgm_cpu_watch_cleanup();
  dcgm_cpu_sock_watch_cleanup();

  free(g_dcgm_sample_cache);
  g_dcgm_sample_cache = NULL;
  free(g_dcgm_sample_valid);
  g_dcgm_sample_valid = NULL;
  free(g_dcgm_last_ts);
  g_dcgm_last_ts = NULL;

  if (g_dcgm_cpu_session_held) {
    monitor_dcgm_session_release();
    g_dcgm_cpu_session_held = 0;
  }
  g_dcgm_handle = (dcgmHandle_t)NULL;
  g_dcgm_cpu_use_disconnect = 0;
  g_dcgm_ready = 0;
}

static void dcgm_backend_cleanup(void)
{
  dcgm_backend_teardown_session();
#ifdef MONITOR_CPU_PAPI_FLOPS
  cpu_counter_metrics_papi_cleanup();
#endif

  free(g_dcgm_ctr0);
  g_dcgm_ctr0 = NULL;
  free(g_dcgm_ctr1);
  g_dcgm_ctr1 = NULL;
  free(g_dcgm_ctr2);
  g_dcgm_ctr2 = NULL;
  free(g_dcgm_ctr3);
  g_dcgm_ctr3 = NULL;
  free(g_dcgm_ctr4);
  g_dcgm_ctr4 = NULL;
  free(g_dcgm_ctr5);
  g_dcgm_ctr5 = NULL;
  free(g_dcgm_inst);
  g_dcgm_inst = NULL;
  free(g_dcgm_aperf);
  g_dcgm_aperf = NULL;
  free(g_dcgm_mperf);
  g_dcgm_mperf = NULL;
  free(g_dcgm_arm_est_flops);
  g_dcgm_arm_est_flops = NULL;
  free(g_dcgm_arm_dram_bytes);
  g_dcgm_arm_dram_bytes = NULL;
  free(g_dcgm_fp_sca_d);
  g_dcgm_fp_sca_d = NULL;
  free(g_dcgm_fp_128_d);
  g_dcgm_fp_128_d = NULL;
  free(g_dcgm_fp_256_d);
  g_dcgm_fp_256_d = NULL;
  free(g_dcgm_fp_512_d);
  g_dcgm_fp_512_d = NULL;
  free(g_dcgm_fp_sca_s);
  g_dcgm_fp_sca_s = NULL;
  free(g_dcgm_fp_128_s);
  g_dcgm_fp_128_s = NULL;
  free(g_dcgm_fp_256_s);
  g_dcgm_fp_256_s = NULL;
  free(g_dcgm_fp_512_s);
  g_dcgm_fp_512_s = NULL;
  free(g_dcjm_prev);
  g_dcjm_prev = NULL;
  free(g_dcjm_cur);
  g_dcjm_cur = NULL;

  if (g_dcgm_proc_stat != NULL) {
    fclose(g_dcgm_proc_stat);
    g_dcgm_proc_stat = NULL;
  }

  g_dcgm_stat_seeded = 0;
  g_dcgm_mono_prev_us = 0;
}

static int dcgm_util_bufs_ok(void)
{
  return (g_dcgm_ctr0 != NULL && g_dcjm_prev != NULL && g_dcjm_cur != NULL) ? 1 : 0;
}

static int dcgm_cpu_watch_install(void)
{
  int chunk, i, start, end;
  dcgmReturn_t rc;

  dcgm_cpu_watch_cleanup();
  if (nr_cpus <= 0)
    return -1;
  g_dcgm_cpu_nchunks = (nr_cpus + DCGM_GROUP_MAX_ENTITIES - 1) / DCGM_GROUP_MAX_ENTITIES;
  g_dcgm_cpu_groups =
      (dcgmGpuGrp_t *)calloc((size_t)g_dcgm_cpu_nchunks, sizeof(*g_dcgm_cpu_groups));
  g_dcgm_cpu_fgs = (dcgmFieldGrp_t *)calloc((size_t)g_dcgm_cpu_nchunks, sizeof(*g_dcgm_cpu_fgs));
  if (g_dcgm_cpu_groups == NULL || g_dcgm_cpu_fgs == NULL) {
    free(g_dcgm_cpu_groups);
    free(g_dcgm_cpu_fgs);
    g_dcgm_cpu_groups = NULL;
    g_dcgm_cpu_fgs = NULL;
    g_dcgm_cpu_nchunks = 0;
    return -1;
  }

  for (chunk = 0; chunk < g_dcgm_cpu_nchunks; chunk++) {
    char gname[32];
    char fname[40];

    snprintf(gname, sizeof(gname), "hpc_cpu_%d", chunk);
    snprintf(fname, sizeof(fname), "hpc_cpu_fg_%d", chunk);
    start = chunk * DCGM_GROUP_MAX_ENTITIES;
    end = start + DCGM_GROUP_MAX_ENTITIES;
    if (end > nr_cpus)
      end = nr_cpus;
    rc = dcgmGroupCreate(g_dcgm_handle, DCGM_GROUP_EMPTY, gname, &g_dcgm_cpu_groups[chunk]);
    if (rc != DCGM_ST_OK)
      goto watch_fail;
    for (i = start; i < end; i++) {
      rc = dcgmGroupAddEntity(g_dcgm_handle, g_dcgm_cpu_groups[chunk], DCGM_FE_CPU_CORE,
                              (dcgm_field_eid_t)i);
      if (rc != DCGM_ST_OK)
        goto watch_fail;
    }
    rc =
        dcgmFieldGroupCreate(g_dcgm_handle, DCGM_CPU_NFIELDS,
                             (unsigned short *)g_dcgm_cpu_field_ids, fname, &g_dcgm_cpu_fgs[chunk]);
    if (rc != DCGM_ST_OK)
      goto watch_fail;
    rc = dcgmWatchFields(g_dcgm_handle, g_dcgm_cpu_groups[chunk], g_dcgm_cpu_fgs[chunk], 1000000LL,
                         3600.0, 3600);
    if (rc != DCGM_ST_OK)
      goto watch_fail;
  }
  (void)dcgmUpdateAllFields(g_dcgm_handle, 1);
  g_dcgm_watch_active = 1;
  return 0;

watch_fail:
  TRACE("DCGM CPU field watch setup failed (rc=%d); using live reads per core\n", (int)rc);
  dcgm_cpu_watch_cleanup();
  return -1;
}

static int dcgm_backend_begin(struct stats_type *type)
{
  size_t n = (size_t)nr_cpus;
  dcgmReturn_t rc;
  time_t now = time(NULL);
  int keep_degraded;
  int need_alloc;

  if (!dcgm_backend_retry_due(now, g_dcgm_retry_after))
    return 0;

  if (g_dcgm_ready)
    return 0;

  keep_degraded = 0;
#ifdef MONITOR_CPU_PAPI_FLOPS
  if (cpu_counter_metrics_papi_ready())
    keep_degraded = 1;
#endif
  if (dcgm_util_bufs_ok())
    keep_degraded = 1;

  /* Soft-failed sessions already tore down the handle; full cleanup only when
   * restarting with no retained util/PAPI state. */
  if (keep_degraded)
    dcgm_backend_teardown_session();
  else
    dcgm_backend_cleanup();

  rc = monitor_dcgm_session_acquire(&g_dcgm_handle, &g_dcgm_cpu_use_disconnect);
  if (rc != DCGM_ST_OK || g_dcgm_handle == (dcgmHandle_t)NULL) {
    ERROR("DCGM CPU backend attach failed\n");
    g_dcgm_handle = (dcgmHandle_t)NULL;
    g_dcgm_cpu_session_held = 0;
    if (!keep_degraded)
      type->st_enabled = 0;
    else
      g_dcgm_retry_after = now + 60;
    return 0;
  }
  g_dcgm_cpu_session_held = 1;

  need_alloc = !dcgm_util_bufs_ok();
  if (need_alloc) {
    g_dcgm_ctr0 = (unsigned long long *)calloc(n, sizeof(*g_dcgm_ctr0));
    g_dcgm_ctr1 = (unsigned long long *)calloc(n, sizeof(*g_dcgm_ctr1));
    g_dcgm_ctr2 = (unsigned long long *)calloc(n, sizeof(*g_dcgm_ctr2));
    g_dcgm_ctr3 = (unsigned long long *)calloc(n, sizeof(*g_dcgm_ctr3));
    g_dcgm_ctr4 = (unsigned long long *)calloc(n, sizeof(*g_dcgm_ctr4));
    g_dcgm_ctr5 = (unsigned long long *)calloc(n, sizeof(*g_dcgm_ctr5));
    g_dcgm_inst = (unsigned long long *)calloc(n, sizeof(*g_dcgm_inst));
    g_dcgm_aperf = (unsigned long long *)calloc(n, sizeof(*g_dcgm_aperf));
    g_dcgm_mperf = (unsigned long long *)calloc(n, sizeof(*g_dcgm_mperf));
    g_dcgm_arm_est_flops = (unsigned long long *)calloc(n, sizeof(*g_dcgm_arm_est_flops));
    g_dcgm_arm_dram_bytes = (unsigned long long *)calloc(n, sizeof(*g_dcgm_arm_dram_bytes));
    g_dcgm_fp_sca_d = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_sca_d));
    g_dcgm_fp_128_d = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_128_d));
    g_dcgm_fp_256_d = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_256_d));
    g_dcgm_fp_512_d = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_512_d));
    g_dcgm_fp_sca_s = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_sca_s));
    g_dcgm_fp_128_s = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_128_s));
    g_dcgm_fp_256_s = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_256_s));
    g_dcgm_fp_512_s = (unsigned long long *)calloc(n, sizeof(*g_dcgm_fp_512_s));
    g_dcjm_prev = (struct dcgm_cpu_jifs *)calloc(n, sizeof(*g_dcjm_prev));
    g_dcjm_cur = (struct dcgm_cpu_jifs *)calloc(n, sizeof(*g_dcjm_cur));
    if (g_dcgm_ctr0 == NULL || g_dcgm_ctr1 == NULL || g_dcgm_ctr2 == NULL || g_dcgm_ctr3 == NULL ||
        g_dcgm_ctr4 == NULL || g_dcgm_ctr5 == NULL || g_dcgm_inst == NULL || g_dcgm_aperf == NULL ||
        g_dcgm_mperf == NULL || g_dcgm_arm_est_flops == NULL || g_dcgm_arm_dram_bytes == NULL ||
        g_dcgm_fp_sca_d == NULL || g_dcgm_fp_128_d == NULL || g_dcgm_fp_256_d == NULL ||
        g_dcgm_fp_512_d == NULL || g_dcgm_fp_sca_s == NULL || g_dcgm_fp_128_s == NULL ||
        g_dcgm_fp_256_s == NULL || g_dcgm_fp_512_s == NULL || g_dcjm_prev == NULL ||
        g_dcjm_cur == NULL) {
      ERROR("DCGM CPU backend allocation failed\n");
      dcgm_backend_cleanup();
      type->st_enabled = 0;
      return 0;
    }
    g_dcgm_mono_prev_us = 0;
    g_dcgm_stat_seeded = 0;
  }

  if (g_dcgm_last_ts == NULL)
    g_dcgm_last_ts = (long long *)calloc(n, sizeof(*g_dcgm_last_ts));
  if (g_dcgm_sample_cache == NULL)
    g_dcgm_sample_cache = (struct dcgm_cpu_sample *)calloc(n, sizeof(*g_dcgm_sample_cache));
  if (g_dcgm_sample_valid == NULL)
    g_dcgm_sample_valid = (unsigned char *)calloc(n, sizeof(*g_dcgm_sample_valid));
  if (g_dcgm_last_ts == NULL || g_dcgm_sample_cache == NULL || g_dcgm_sample_valid == NULL) {
    ERROR("DCGM CPU sample cache allocation failed\n");
    if (!keep_degraded) {
      dcgm_backend_cleanup();
      type->st_enabled = 0;
    } else {
      dcgm_backend_teardown_session();
    }
    return 0;
  }

  if (dcgm_cpu_watch_install() != 0)
    TRACE("DCGM CPU watch not active; samples may use slower live queries\n");
  if (dcgm_topology_build_sock_power_map() != 0)
    TRACE("DCGM CPU socket power mapping unavailable (sysfs packages vs DCGM_FE_CPU)\n");
  else if (dcgm_cpu_sock_watch_install() != 0)
    TRACE("DCGM CPU socket power field watch not active; using entity reads\n");
#ifdef MONITOR_CPU_PAPI_FLOPS
  if (!cpu_counter_metrics_papi_ready()) {
    if (cpu_counter_metrics_papi_begin(type) != 0)
      TRACE("PAPI FLOPs/cycles begin returned error; continuing with DCGM util/power only\n");
  }
#endif
  g_dcgm_ready = 1;
  g_dcgm_retry_after = 0;
  type->st_enabled = 1;
  return 0;
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

#ifdef MONITOR_CPU_BACKEND_DCGM
  long long delta_us_collect = 0;
  int proc_stat_ok = 0;
  dcgmReturn_t update_rc = DCGM_ST_OK;
  struct timespec t0, t1;
  long long update_elapsed_us = 0;
  int papi_ready = 0;
  time_t now = time(NULL);

#ifdef MONITOR_CPU_PAPI_FLOPS
  papi_ready = cpu_counter_metrics_papi_ready();
#endif

  /* Re-init DCGM after soft-fail backoff without requiring daemon restart. */
  if (!g_dcgm_ready && dcgm_backend_retry_due(now, g_dcgm_retry_after))
    (void)dcgm_backend_begin(type);

#ifdef MONITOR_CPU_PAPI_FLOPS
  papi_ready = cpu_counter_metrics_papi_ready();
#endif

  if (g_dcgm_ready) {
    if (clock_gettime(CLOCK_MONOTONIC, &t0) != 0) {
      t0.tv_sec = 0;
      t0.tv_nsec = 0;
    }
    update_rc = dcgmUpdateAllFields(g_dcgm_handle, 0);
    if (clock_gettime(CLOCK_MONOTONIC, &t1) == 0 && (t0.tv_sec > 0 || t0.tv_nsec > 0))
      update_elapsed_us = ((long long)t1.tv_sec - (long long)t0.tv_sec) * 1000000LL +
                          ((long long)t1.tv_nsec - (long long)t0.tv_nsec) / 1000LL;
    if (update_elapsed_us > 500000LL)
      monitor_log_warn("cpu_counter_metrics: dcgmUpdateAllFields slow path elapsed_us=%lld\n",
                       update_elapsed_us);
    if (update_rc != DCGM_ST_OK) {
      g_dcgm_update_failures++;
      monitor_log_warn("cpu_counter_metrics: dcgmUpdateAllFields failed rc=%d (failures=%lu); "
                       "soft-reset DCGM (keep PAPI/util)\n",
                       (int)update_rc, g_dcgm_update_failures);
      dcgm_backend_teardown_session();
      g_dcgm_retry_after = time(NULL) + 60;
    }
  }
  if (g_dcgm_ready && g_dcgm_ncpu_entities > 0)
    dcgm_cpu_refresh_socket_power();
  if (dcgm_util_bufs_ok())
    proc_stat_ok = (dcgm_proc_stat_read_cpus(g_dcjm_cur, nr_cpus) == 0);
  if (g_dcgm_ready)
    dcgm_cpu_refresh_sample_cache();
  if (dcgm_host_cpu_hw_collect_active(g_dcgm_ready, papi_ready, dcgm_util_bufs_ok())) {
    struct timespec mono;

    if (clock_gettime(CLOCK_MONOTONIC, &mono) == 0) {
      long long mono_us_collect =
          (long long)mono.tv_sec * 1000000LL + (long long)mono.tv_nsec / 1000LL;

      if (g_dcgm_mono_prev_us > 0 && mono_us_collect > g_dcgm_mono_prev_us)
        delta_us_collect = mono_us_collect - g_dcgm_mono_prev_us;
      g_dcgm_mono_prev_us = mono_us_collect;
    }
  }
#endif
#ifndef MONITOR_CPU_BACKEND_DCGM
  {
    int skip_pmc_reads = 0;

    /* Re-program core LIKWID group once per tick after DF/RAPL setupCounters. */
    if (cpu_counter_metrics_likwid_ready() && likwid_pmc_adapter_prepare_collect() != 0)
      skip_pmc_reads = 1;

    for (i = 0; i < nr_cpus; i++) {
      char cpu[80];
      struct stats *stats;
      snprintf(cpu, sizeof(cpu), "%d", i);
      stats = get_current_stats(type, cpu);
      if (stats == NULL)
        continue;
      /* LIKWID-only: no MSR fallback when setup failed or read_cpu fails. */
      if (!skip_pmc_reads && cpu_counter_metrics_likwid_ready()) {
        uint64_t ctls[8] = {0};
        (void)likwid_pmc_adapter_read_cpu(stats, i, ctls, 8, 8);
      }
    }
  }
#else
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    struct stats *stats;
    snprintf(cpu, sizeof(cpu), "%d", i);
    stats = get_current_stats(type, cpu);
    if (stats == NULL)
      continue;
    if (!dcgm_host_cpu_hw_collect_active(g_dcgm_ready, papi_ready, dcgm_util_bufs_ok()))
      continue;
    {
      struct dcgm_cpu_sample sample;
      long long delta_us = delta_us_collect;
      int rd = -1;

      memset(&sample, 0, sizeof(sample));
      if (g_dcgm_ready) {
        if (g_dcgm_watch_active && g_dcgm_sample_valid != NULL && g_dcgm_sample_cache != NULL &&
            g_dcgm_sample_valid[i]) {
          sample = g_dcgm_sample_cache[i];
          rd = 0;
        } else {
          rd = read_dcgm_cpu_sample(i, &sample);
        }
        if (rd == 0)
          dcgm_cpu_scale_util_if_fraction(&sample);
      }
      if ((rd != 0 || sample.util_total <= 0.0) && proc_stat_ok && g_dcgm_stat_seeded)
        dcgm_cpu_sample_from_jiffy_diff(&sample, &g_dcjm_cur[i], &g_dcjm_prev[i]);

      if (rd == 0 && sample.ts > 0) {
        if (g_dcgm_last_ts != NULL && g_dcgm_last_ts[i] > 0 && sample.ts > g_dcgm_last_ts[i]) {
          long long dts = sample.ts - g_dcgm_last_ts[i];

          if (dts > 0 && dts < 3600LL * 1000000LL)
            delta_us = dts;
        }
        if (g_dcgm_last_ts != NULL)
          g_dcgm_last_ts[i] = sample.ts;
      }

      if (sample.clock_khz <= 0.0)
        sample.clock_khz = dcgm_cpu_nominal_freq_khz(i);

      if (dcgm_util_bufs_ok()) {
        dcgm_accumulate_from_util_sample(i, &sample, delta_us);
        publish_dcgm_cpu_stats(stats, i);
      }
#ifdef MONITOR_CPU_PAPI_FLOPS
      if (papi_ready)
        cpu_counter_metrics_papi_collect_cpu(stats, i);
#endif
    }
  }
#endif
#ifdef MONITOR_CPU_BACKEND_DCGM
  if (dcgm_util_bufs_ok() && proc_stat_ok && nr_cpus > 0) {
    memcpy(g_dcjm_prev, g_dcjm_cur, (size_t)nr_cpus * sizeof(*g_dcjm_prev));
    g_dcgm_stat_seeded = 1;
  }
#endif
}

struct stats_type cpu_counter_metrics_stats_type = {
    .st_begin = &cpu_counter_metrics_begin,
    .st_collect = &cpu_counter_metrics_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(CPU_COUNTER_METRICS_KEYS),
#undef X
    .st_name = CPU_COUNTER_METRICS_ST_NAME,
};
