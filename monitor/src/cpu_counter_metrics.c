#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <ctype.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#ifdef MONITOR_CPU_BACKEND_DCGM
#include "dcgm_agent.h"
#include "dcgm_fields.h"
#include "dcgm_structs.h"
#include "dcgm_session.h"
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
static dcgmHandle_t g_dcgm_handle = (dcgmHandle_t) NULL;
static int g_dcgm_cpu_use_disconnect = 0;
static dcgmGpuGrp_t *g_dcgm_cpu_groups = NULL;
static dcgmFieldGrp_t *g_dcgm_cpu_fgs = NULL;
static int g_dcgm_cpu_nchunks = 0;
static unsigned long long *g_dcgm_ctr0 = NULL;
static unsigned long long *g_dcgm_ctr1 = NULL;
static unsigned long long *g_dcgm_ctr2 = NULL;
static unsigned long long *g_dcgm_ctr3 = NULL;
static unsigned long long *g_dcgm_ctr4 = NULL;
static unsigned long long *g_dcgm_ctr5 = NULL;
static unsigned long long *g_dcgm_inst = NULL;
static unsigned long long *g_dcgm_aperf = NULL;
static unsigned long long *g_dcgm_mperf = NULL;
static unsigned long long *g_dcgm_arm_est_flops = NULL;
static unsigned long long *g_dcgm_arm_dram_bytes = NULL;
static unsigned long long *g_dcgm_fp_sca_d = NULL;
static unsigned long long *g_dcgm_fp_128_d = NULL;
static unsigned long long *g_dcgm_fp_256_d = NULL;
static unsigned long long *g_dcgm_fp_512_d = NULL;
static unsigned long long *g_dcgm_fp_sca_s = NULL;
static unsigned long long *g_dcgm_fp_128_s = NULL;
static unsigned long long *g_dcgm_fp_256_s = NULL;
static unsigned long long *g_dcgm_fp_512_s = NULL;
static long long *g_dcgm_last_ts = NULL;
static long long g_dcgm_mono_prev_us = 0;

/* DCGM_FE_CPU (socket) power: fields 1130/1131; mapped to logical CPUs via sysfs packages. */
static dcgmGpuGrp_t g_dcgm_sock_group = (dcgmGpuGrp_t) NULL;
static dcgmFieldGrp_t g_dcgm_sock_fg = (dcgmFieldGrp_t) NULL;
static int *g_dcgm_cpu_entity_list = NULL;
static int g_dcgm_ncpu_entities = 0;
static int *g_dcgm_logical_to_power_slot = NULL;
static double *g_dcgm_sock_power_util = NULL;
static double *g_dcgm_sock_power_limit = NULL;
static int g_dcgm_sock_map_mismatch_logged = 0;
static const unsigned short g_dcgm_cpu_power_field_ids[] = {
  DCGM_FI_DEV_CPU_POWER_UTIL_CURRENT,
  DCGM_FI_DEV_CPU_POWER_LIMIT
};
#define DCGM_CPU_POWER_NFIELDS \
  ((unsigned int) (sizeof(g_dcgm_cpu_power_field_ids) / sizeof(g_dcgm_cpu_power_field_ids[0])))

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

struct dcgm_cpu_jifs {
  unsigned long long u, nice, sys, idle, iow, irq, sft, stl, gu, gn;
};

static struct dcgm_cpu_jifs *g_dcjm_prev = NULL;
static struct dcgm_cpu_jifs *g_dcjm_cur = NULL;
static int g_dcgm_stat_seeded = 0;
static FILE *g_dcgm_proc_stat = NULL;

static const unsigned short g_dcgm_cpu_field_ids[] = {
  DCGM_FI_DEV_CPU_UTIL_TOTAL,
  DCGM_FI_DEV_CPU_UTIL_USER,
  DCGM_FI_DEV_CPU_UTIL_NICE,
  DCGM_FI_DEV_CPU_UTIL_SYS,
  DCGM_FI_DEV_CPU_UTIL_IRQ,
  DCGM_FI_DEV_CPU_CLOCK_CURRENT
};

#define DCGM_CPU_NFIELDS ((unsigned int) (sizeof(g_dcgm_cpu_field_ids) / sizeof(g_dcgm_cpu_field_ids[0])))

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
  return (double) khz;
}

static double dcgm_cpu_nominal_freq_khz(int core_id)
{
  char path[160];
  double khz;

  snprintf(path, sizeof(path),
	   "/sys/devices/system/cpu/cpu%d/cpufreq/base_frequency", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  snprintf(path, sizeof(path),
	   "/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  snprintf(path, sizeof(path),
	   "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_max_freq", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  snprintf(path, sizeof(path),
	   "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq", core_id);
  khz = dcgm_cpu_read_khz_sysfs_path(path);
  if (khz > 0.0)
    return khz;
  if (core_id != 0)
    return dcgm_cpu_nominal_freq_khz(0);
  return 0.0;
}

static unsigned long long dcgm_jifs_total(const struct dcgm_cpu_jifs *j)
{
  return j->u + j->nice + j->sys + j->idle + j->iow + j->irq + j->sft + j->stl + j->gu + j->gn;
}

static unsigned long long dcgm_jifs_nid(const struct dcgm_cpu_jifs *j)
{
  return j->u + j->nice + j->sys + j->irq + j->sft + j->stl + j->gu + j->gn;
}

static void dcgm_cpu_scale_util_if_fraction(struct dcgm_cpu_sample *s)
{
  if (s->util_total <= 0.0)
    return;
  if (s->util_total > 1.0001)
    return;
  s->util_total *= 100.0;
  s->util_user *= 100.0;
  s->util_nice *= 100.0;
  s->util_sys *= 100.0;
  s->util_irq *= 100.0;
}

static void dcgm_cpu_sample_from_jiffy_diff(struct dcgm_cpu_sample *s, const struct dcgm_cpu_jifs *cur,
					    const struct dcgm_cpu_jifs *prev)
{
  unsigned long long pt = dcgm_jifs_total(prev);
  unsigned long long ct = dcgm_jifs_total(cur);
  unsigned long long pn = dcgm_jifs_nid(prev);
  unsigned long long cn = dcgm_jifs_nid(cur);
  unsigned long long d_tot, d_nid;
  unsigned long long d_u, d_ni, d_sy, d_iq, d_sft;

  if (ct < pt || cn < pn)
    return;
  d_tot = ct - pt;
  d_nid = cn - pn;
  if (d_tot == 0)
    return;
  s->util_total = clamp_percent(100.0 * (double) d_nid / (double) d_tot);
  d_u = (cur->u >= prev->u) ? (cur->u - prev->u) : 0;
  d_ni = (cur->nice >= prev->nice) ? (cur->nice - prev->nice) : 0;
  d_sy = (cur->sys >= prev->sys) ? (cur->sys - prev->sys) : 0;
  d_iq = (cur->irq >= prev->irq) ? (cur->irq - prev->irq) : 0;
  d_sft = (cur->sft >= prev->sft) ? (cur->sft - prev->sft) : 0;
  s->util_user = clamp_percent(100.0 * (double) (d_u + d_ni) / (double) d_tot);
  s->util_sys = clamp_percent(100.0 * (double) d_sy / (double) d_tot);
  s->util_irq = clamp_percent(100.0 * (double) (d_iq + d_sft) / (double) d_tot);
  s->util_nice = 0.0;
}

static int dcgm_proc_stat_read_cpus(struct dcgm_cpu_jifs *out, int ncpus)
{
  char *line = NULL;
  size_t line_size = 0;
  int any = 0;

  if (ncpus <= 0 || out == NULL)
    return -1;
  memset(out, 0, (size_t) ncpus * sizeof(*out));
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
    if (!isdigit((unsigned char) *p))
      continue;
    idcpu = (int) strtol(p, &p, 10);
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
	break;
      default: break;
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
    v = (values[f].fieldType == DCGM_FT_DOUBLE) ? values[f].value.dbl : (double) values[f].value.i64;
    if (values[f].ts > s->ts)
      s->ts = values[f].ts;
    switch (values[f].fieldId) {
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
	break;
      default: break;
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
  ent.entityId = (dcgm_field_eid_t) core_id;
  for (fi = 0; fi < DCGM_CPU_NFIELDS; fi++)
    values[fi].version = dcgmFieldValue_version2;

  rc = dcgmEntitiesGetLatestValues(g_dcgm_handle, &ent, 1,
				   (unsigned short *) g_dcgm_cpu_field_ids, DCGM_CPU_NFIELDS,
				   DCGM_FV_FLAG_LIVE_DATA, values);
  if (rc != DCGM_ST_OK)
    return -1;
  return dcgm_cpu_fill_sample_from_v2(values, DCGM_CPU_NFIELDS, s);
}

static int read_dcgm_cpu_sample(int core_id, struct dcgm_cpu_sample *s)
{
  dcgmFieldValue_v1 values[DCGM_CPU_NFIELDS];

  memset(values, 0, sizeof(values));
  if (dcgmEntityGetLatestValues(g_dcgm_handle,
				DCGM_FE_CPU_CORE,
				core_id,
				(unsigned short *) g_dcgm_cpu_field_ids,
				DCGM_CPU_NFIELDS,
				values) == DCGM_ST_OK
      && dcgm_cpu_fill_sample_from_v1(DCGM_CPU_NFIELDS, g_dcgm_cpu_field_ids, values, s) == 0) {
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

static int dcgm_cmp_int(const void *a, const void *b)
{
  int x = *(const int *) a;
  int y = *(const int *) b;

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
  snprintf(path, sizeof(path),
	   "/sys/devices/system/cpu/cpu%d/topology/physical_package_id", cpu_idx);
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
  *pkg_out = (int) v;
  return 0;
}

static unsigned long long dcgm_watts_dbl_to_ull(double v)
{
  if (v <= 0.0)
    return 0ULL;
  if (v >= (double) ULLONG_MAX)
    return ULLONG_MAX;
  return (unsigned long long) (v + 0.5);
}

static void dcgm_cpu_sock_watch_cleanup(void)
{
  if (g_dcgm_handle != (dcgmHandle_t) NULL) {
    if (g_dcgm_sock_fg != (dcgmFieldGrp_t) NULL)
      (void) dcgmFieldGroupDestroy(g_dcgm_handle, g_dcgm_sock_fg);
    if (g_dcgm_sock_group != (dcgmGpuGrp_t) NULL)
      (void) dcgmGroupDestroy(g_dcgm_handle, g_dcgm_sock_group);
  }
  g_dcgm_sock_fg = (dcgmFieldGrp_t) NULL;
  g_dcgm_sock_group = (dcgmGpuGrp_t) NULL;
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
  if (nr_cpus <= 0 || g_dcgm_handle == (dcgmHandle_t) NULL)
    return -1;

  pkg_per_cpu = (int *) calloc((size_t) nr_cpus, sizeof(*pkg_per_cpu));
  sorted_pkgs = (int *) calloc((size_t) nr_cpus, sizeof(*sorted_pkgs));
  unique_pkg = (int *) calloc((size_t) nr_cpus, sizeof(*unique_pkg));
  g_dcgm_logical_to_power_slot = (int *) calloc((size_t) nr_cpus, sizeof(*g_dcgm_logical_to_power_slot));
  if (pkg_per_cpu == NULL || sorted_pkgs == NULL || unique_pkg == NULL
      || g_dcgm_logical_to_power_slot == NULL) {
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
  qsort(sorted_pkgs, (size_t) nr_cpus, sizeof(*sorted_pkgs), dcgm_cmp_int);
  nu = 0;
  for (i = 0; i < nr_cpus; i++) {
    if (i == 0 || sorted_pkgs[i] != sorted_pkgs[i - 1])
      unique_pkg[nu++] = sorted_pkgs[i];
  }

  n_ent = 32;
  ent_buf = (dcgm_field_eid_t *) calloc((size_t) n_ent, sizeof(*ent_buf));
  if (ent_buf == NULL)
    goto map_fail;
  rc = dcgmGetEntityGroupEntities(g_dcgm_handle, DCGM_FE_CPU, ent_buf, &n_ent, 0);
  if (rc == DCGM_ST_INSUFFICIENT_SIZE && n_ent > 0) {
    free(ent_buf);
    ent_buf = (dcgm_field_eid_t *) calloc((size_t) n_ent, sizeof(*ent_buf));
    if (ent_buf == NULL)
      goto map_fail;
    rc = dcgmGetEntityGroupEntities(g_dcgm_handle, DCGM_FE_CPU, ent_buf, &n_ent, 0);
  }
  if (rc != DCGM_ST_OK || n_ent <= 0) {
    TRACE("DCGM_FE_CPU enumeration failed (rc=%d n=%d); Grace CPU power fields skipped\n",
	  (int) rc, n_ent);
    goto map_fail;
  }

  g_dcgm_cpu_entity_list = (int *) calloc((size_t) n_ent, sizeof(*g_dcgm_cpu_entity_list));
  g_dcgm_sock_power_util = (double *) calloc((size_t) n_ent, sizeof(*g_dcgm_sock_power_util));
  g_dcgm_sock_power_limit = (double *) calloc((size_t) n_ent, sizeof(*g_dcgm_sock_power_limit));
  if (g_dcgm_cpu_entity_list == NULL || g_dcgm_sock_power_util == NULL
      || g_dcgm_sock_power_limit == NULL)
    goto map_fail;

  for (i = 0; i < n_ent; i++)
    g_dcgm_cpu_entity_list[i] = (int) ent_buf[i];
  free(ent_buf);
  ent_buf = NULL;
  qsort(g_dcgm_cpu_entity_list, (size_t) n_ent, sizeof(*g_dcgm_cpu_entity_list), dcgm_cmp_int);

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

  if (g_dcgm_handle == (dcgmHandle_t) NULL || g_dcgm_ncpu_entities <= 0
      || g_dcgm_cpu_entity_list == NULL)
    return -1;

  rc = dcgmGroupCreate(g_dcgm_handle, DCGM_GROUP_EMPTY, "hpc_cpu_sock", &g_dcgm_sock_group);
  if (rc != DCGM_ST_OK)
    goto sock_fail;
  for (j = 0; j < g_dcgm_ncpu_entities; j++) {
    rc = dcgmGroupAddEntity(g_dcgm_handle, g_dcgm_sock_group, DCGM_FE_CPU,
			    (dcgm_field_eid_t) g_dcgm_cpu_entity_list[j]);
    if (rc != DCGM_ST_OK)
      goto sock_fail;
  }
  rc = dcgmFieldGroupCreate(g_dcgm_handle, DCGM_CPU_POWER_NFIELDS,
			    (unsigned short *) g_dcgm_cpu_power_field_ids, "hpc_cpu_sock_fg",
			    &g_dcgm_sock_fg);
  if (rc != DCGM_ST_OK)
    goto sock_fail;
  rc = dcgmWatchFields(g_dcgm_handle, g_dcgm_sock_group, g_dcgm_sock_fg, 1000000LL, 3600.0, 3600);
  if (rc != DCGM_ST_OK)
    goto sock_fail;
  return 0;

sock_fail:
  TRACE("DCGM CPU socket power watch failed (rc=%d); using per-entity reads\n", (int) rc);
  if (g_dcgm_sock_fg != (dcgmFieldGrp_t) NULL)
    (void) dcgmFieldGroupDestroy(g_dcgm_handle, g_dcgm_sock_fg);
  if (g_dcgm_sock_group != (dcgmGpuGrp_t) NULL)
    (void) dcgmGroupDestroy(g_dcgm_handle, g_dcgm_sock_group);
  g_dcgm_sock_fg = (dcgmFieldGrp_t) NULL;
  g_dcgm_sock_group = (dcgmGpuGrp_t) NULL;
  return -1;
}

static void dcgm_cpu_refresh_socket_power(void)
{
  int j;

  if (g_dcgm_handle == (dcgmHandle_t) NULL || g_dcgm_ncpu_entities <= 0
      || g_dcgm_cpu_entity_list == NULL || g_dcgm_sock_power_util == NULL
      || g_dcgm_sock_power_limit == NULL)
    return;

  for (j = 0; j < g_dcgm_ncpu_entities; j++) {
    dcgmFieldValue_v1 vals[DCGM_CPU_POWER_NFIELDS];
    int eid = g_dcgm_cpu_entity_list[j];
    double u = 0.0, lim = 0.0;

    memset(vals, 0, sizeof(vals));
    if (dcgmEntityGetLatestValues(g_dcgm_handle, DCGM_FE_CPU, eid,
				  (unsigned short *) g_dcgm_cpu_power_field_ids,
				  DCGM_CPU_POWER_NFIELDS, vals) != DCGM_ST_OK) {
      g_dcgm_sock_power_util[j] = 0.0;
      g_dcgm_sock_power_limit[j] = 0.0;
      continue;
    }
    if (vals[0].status == DCGM_ST_OK) {
      if (vals[0].fieldType == DCGM_FT_DOUBLE)
	u = vals[0].value.dbl;
      else
	u = (double) vals[0].value.i64;
    }
    if (vals[1].status == DCGM_ST_OK) {
      if (vals[1].fieldType == DCGM_FT_DOUBLE)
	lim = vals[1].value.dbl;
      else
	lim = (double) vals[1].value.i64;
    }
    if (u < 0.0)
      u = 0.0;
    if (lim < 0.0)
      lim = 0.0;
    g_dcgm_sock_power_util[j] = u;
    g_dcgm_sock_power_limit[j] = lim;
  }
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
  /* Match Intel LIKWID FIXC0..2 mapping (INSTR_RETIRED / core unhalted / ref). */
  stats_set(stats, "FIXED_CTR0", g_dcgm_inst[i]);
  stats_set(stats, "FIXED_CTR1", g_dcgm_aperf[i]);
  stats_set(stats, "FIXED_CTR2", g_dcgm_mperf[i]);
  stats_set(stats, "INST_RETIRED", g_dcgm_inst[i]);
  stats_set(stats, "APERF", g_dcgm_aperf[i]);
  stats_set(stats, "MPERF", g_dcgm_mperf[i]);
  stats_set(stats, "DF_CTR0", 0);
  stats_set(stats, "DF_CTR1", 0);
  stats_set(stats, "DF_CTR2", 0);
  stats_set(stats, "DF_CTR3", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE", g_dcgm_fp_sca_d[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE", g_dcgm_fp_128_d[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE", g_dcgm_fp_256_d[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE", g_dcgm_fp_512_d[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_SCALAR_SINGLE", g_dcgm_fp_sca_s[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE", g_dcgm_fp_128_s[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE", g_dcgm_fp_256_s[i]);
  stats_set(stats, "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE", g_dcgm_fp_512_s[i]);
  stats_set(stats, "ARM_EST_FLOPS", g_dcgm_arm_est_flops[i]);
  stats_set(stats, "ARM_DRAM_BW_BYTES", g_dcgm_arm_dram_bytes[i]);
  if (g_dcgm_logical_to_power_slot != NULL && i >= 0 && i < nr_cpus) {
    int slot = g_dcgm_logical_to_power_slot[i];

    if (slot >= 0 && slot < g_dcgm_ncpu_entities && g_dcgm_sock_power_util != NULL
	&& g_dcgm_sock_power_limit != NULL) {
      stats_set(stats, "DCGM_CPU_POWER_UTIL_W",
		dcgm_watts_dbl_to_ull(g_dcgm_sock_power_util[slot]));
      stats_set(stats, "DCGM_CPU_POWER_LIMIT_W",
		dcgm_watts_dbl_to_ull(g_dcgm_sock_power_limit[slot]));
    } else {
      stats_set(stats, "DCGM_CPU_POWER_UTIL_W", 0ULL);
      stats_set(stats, "DCGM_CPU_POWER_LIMIT_W", 0ULL);
    }
  } else {
    stats_set(stats, "DCGM_CPU_POWER_UTIL_W", 0ULL);
    stats_set(stats, "DCGM_CPU_POWER_LIMIT_W", 0ULL);
  }
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
  g_dcgm_arm_est_flops[i] +=
      (unsigned long long) ((act_cycles * ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE) + 0.5);
  g_dcgm_arm_dram_bytes[i] +=
      (unsigned long long) ((act_cycles * ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE) + 0.5);
  {
    double total_flops = act_cycles * ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE;
    double flops64 = total_flops * ARM_APPROX_FP64_FLOP_SHARE;
    double flops32 = total_flops - flops64;
    double flops64_vec = flops64 * ARM_APPROX_FP64_VECTOR_FLOP_SHARE;
    double flops64_sca = flops64 - flops64_vec;
    double flops32_vec = flops32 * ARM_APPROX_FP32_VECTOR_FLOP_SHARE;
    double flops32_sca = flops32 - flops32_vec;
    /* Map vector FLOPs to 128b packed buckets by default (2x64b, 4x32b). */
    g_dcgm_fp_sca_d[i] += (unsigned long long) (flops64_sca + 0.5);
    g_dcgm_fp_128_d[i] += (unsigned long long) (flops64_vec / 2.0 + 0.5);
    g_dcgm_fp_sca_s[i] += (unsigned long long) (flops32_sca + 0.5);
    g_dcgm_fp_128_s[i] += (unsigned long long) (flops32_vec / 4.0 + 0.5);
  }
}

static void dcgm_cpu_watch_cleanup(void)
{
  int c;

  dcgm_cpu_sock_watch_cleanup();
  if (g_dcgm_cpu_groups == NULL && g_dcgm_cpu_fgs == NULL)
    return;
  if (g_dcgm_handle != (dcgmHandle_t) NULL && g_dcgm_cpu_nchunks > 0 && g_dcgm_cpu_groups != NULL
      && g_dcgm_cpu_fgs != NULL) {
    for (c = 0; c < g_dcgm_cpu_nchunks; c++) {
      if (g_dcgm_cpu_fgs[c] != (dcgmFieldGrp_t) NULL)
	(void) dcgmFieldGroupDestroy(g_dcgm_handle, g_dcgm_cpu_fgs[c]);
      if (g_dcgm_cpu_groups[c] != (dcgmGpuGrp_t) NULL)
	(void) dcgmGroupDestroy(g_dcgm_handle, g_dcgm_cpu_groups[c]);
    }
  }
  free(g_dcgm_cpu_groups);
  free(g_dcgm_cpu_fgs);
  g_dcgm_cpu_groups = NULL;
  g_dcgm_cpu_fgs = NULL;
  g_dcgm_cpu_nchunks = 0;
}

static void dcgm_backend_cleanup(void)
{
  dcgm_cpu_watch_cleanup();

  free(g_dcgm_ctr0); g_dcgm_ctr0 = NULL;
  free(g_dcgm_ctr1); g_dcgm_ctr1 = NULL;
  free(g_dcgm_ctr2); g_dcgm_ctr2 = NULL;
  free(g_dcgm_ctr3); g_dcgm_ctr3 = NULL;
  free(g_dcgm_ctr4); g_dcgm_ctr4 = NULL;
  free(g_dcgm_ctr5); g_dcgm_ctr5 = NULL;
  free(g_dcgm_inst); g_dcgm_inst = NULL;
  free(g_dcgm_aperf); g_dcgm_aperf = NULL;
  free(g_dcgm_mperf); g_dcgm_mperf = NULL;
  free(g_dcgm_arm_est_flops); g_dcgm_arm_est_flops = NULL;
  free(g_dcgm_arm_dram_bytes); g_dcgm_arm_dram_bytes = NULL;
  free(g_dcgm_fp_sca_d); g_dcgm_fp_sca_d = NULL;
  free(g_dcgm_fp_128_d); g_dcgm_fp_128_d = NULL;
  free(g_dcgm_fp_256_d); g_dcgm_fp_256_d = NULL;
  free(g_dcgm_fp_512_d); g_dcgm_fp_512_d = NULL;
  free(g_dcgm_fp_sca_s); g_dcgm_fp_sca_s = NULL;
  free(g_dcgm_fp_128_s); g_dcgm_fp_128_s = NULL;
  free(g_dcgm_fp_256_s); g_dcgm_fp_256_s = NULL;
  free(g_dcgm_fp_512_s); g_dcgm_fp_512_s = NULL;
  free(g_dcgm_last_ts); g_dcgm_last_ts = NULL;
  free(g_dcjm_prev); g_dcjm_prev = NULL;
  free(g_dcjm_cur); g_dcjm_cur = NULL;

  if (g_dcgm_proc_stat != NULL) {
    fclose(g_dcgm_proc_stat);
    g_dcgm_proc_stat = NULL;
  }

  if (g_dcgm_handle != (dcgmHandle_t) NULL) {
    if (g_dcgm_cpu_use_disconnect)
      (void) dcgmDisconnect(g_dcgm_handle);
    else
      (void) dcgmStopEmbedded(g_dcgm_handle);
    g_dcgm_handle = (dcgmHandle_t) NULL;
  }
  (void) dcgmShutdown();

  g_dcgm_cpu_use_disconnect = 0;
  g_dcgm_stat_seeded = 0;
  g_dcgm_mono_prev_us = 0;
  g_dcgm_ready = 0;
}

static int dcgm_cpu_watch_install(void)
{
  int chunk, i, start, end;
  dcgmReturn_t rc;

  dcgm_cpu_watch_cleanup();
  if (nr_cpus <= 0)
    return -1;
  g_dcgm_cpu_nchunks = (nr_cpus + DCGM_GROUP_MAX_ENTITIES - 1) / DCGM_GROUP_MAX_ENTITIES;
  g_dcgm_cpu_groups = (dcgmGpuGrp_t *) calloc((size_t) g_dcgm_cpu_nchunks, sizeof(*g_dcgm_cpu_groups));
  g_dcgm_cpu_fgs = (dcgmFieldGrp_t *) calloc((size_t) g_dcgm_cpu_nchunks, sizeof(*g_dcgm_cpu_fgs));
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
			      (dcgm_field_eid_t) i);
      if (rc != DCGM_ST_OK)
	goto watch_fail;
    }
    rc = dcgmFieldGroupCreate(g_dcgm_handle, DCGM_CPU_NFIELDS,
			      (unsigned short *) g_dcgm_cpu_field_ids, fname, &g_dcgm_cpu_fgs[chunk]);
    if (rc != DCGM_ST_OK)
      goto watch_fail;
    rc = dcgmWatchFields(g_dcgm_handle, g_dcgm_cpu_groups[chunk], g_dcgm_cpu_fgs[chunk], 1000000LL,
			 3600.0, 3600);
    if (rc != DCGM_ST_OK)
      goto watch_fail;
  }
  (void) dcgmUpdateAllFields(g_dcgm_handle, 1);
  return 0;

watch_fail:
  TRACE("DCGM CPU field watch setup failed (rc=%d); using live reads per core\n", (int) rc);
  dcgm_cpu_watch_cleanup();
  return -1;
}

static int dcgm_backend_begin(struct stats_type *type)
{
  size_t n = (size_t) nr_cpus;
  dcgmReturn_t rc;
  time_t now = time(NULL);

  if (g_dcgm_retry_after > 0 && now > 0 && now < g_dcgm_retry_after) {
    type->st_enabled = 0;
    return 0;
  }

  if (g_dcgm_ready)
    return 0;
  /* Defensive: if prior init failed halfway, release leftovers first. */
  dcgm_backend_cleanup();

  rc = dcgmInit();
  if (rc != DCGM_ST_OK) {
    ERROR("DCGM CPU backend init failed\n");
    type->st_enabled = 0;
    return 0;
  }
  rc = monitor_dcgm_attach_for_process(&g_dcgm_handle, &g_dcgm_cpu_use_disconnect);
  if (rc != DCGM_ST_OK || g_dcgm_handle == (dcgmHandle_t) NULL) {
    ERROR("DCGM CPU backend attach failed\n");
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
  g_dcgm_arm_est_flops = (unsigned long long *) calloc(n, sizeof(*g_dcgm_arm_est_flops));
  g_dcgm_arm_dram_bytes = (unsigned long long *) calloc(n, sizeof(*g_dcgm_arm_dram_bytes));
  g_dcgm_fp_sca_d = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_sca_d));
  g_dcgm_fp_128_d = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_128_d));
  g_dcgm_fp_256_d = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_256_d));
  g_dcgm_fp_512_d = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_512_d));
  g_dcgm_fp_sca_s = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_sca_s));
  g_dcgm_fp_128_s = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_128_s));
  g_dcgm_fp_256_s = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_256_s));
  g_dcgm_fp_512_s = (unsigned long long *) calloc(n, sizeof(*g_dcgm_fp_512_s));
  g_dcgm_last_ts = (long long *) calloc(n, sizeof(*g_dcgm_last_ts));
  g_dcjm_prev = (struct dcgm_cpu_jifs *) calloc(n, sizeof(*g_dcjm_prev));
  g_dcjm_cur = (struct dcgm_cpu_jifs *) calloc(n, sizeof(*g_dcjm_cur));
  if (g_dcgm_ctr0 == NULL || g_dcgm_ctr1 == NULL || g_dcgm_ctr2 == NULL ||
      g_dcgm_ctr3 == NULL || g_dcgm_ctr4 == NULL || g_dcgm_ctr5 == NULL ||
      g_dcgm_inst == NULL || g_dcgm_aperf == NULL || g_dcgm_mperf == NULL ||
      g_dcgm_arm_est_flops == NULL || g_dcgm_arm_dram_bytes == NULL ||
      g_dcgm_fp_sca_d == NULL || g_dcgm_fp_128_d == NULL ||
      g_dcgm_fp_256_d == NULL || g_dcgm_fp_512_d == NULL ||
      g_dcgm_fp_sca_s == NULL || g_dcgm_fp_128_s == NULL ||
      g_dcgm_fp_256_s == NULL || g_dcgm_fp_512_s == NULL ||
      g_dcgm_last_ts == NULL || g_dcjm_prev == NULL || g_dcjm_cur == NULL) {
    ERROR("DCGM CPU backend allocation failed\n");
    dcgm_backend_cleanup();
    type->st_enabled = 0;
    return 0;
  }
  if (dcgm_cpu_watch_install() != 0)
    TRACE("DCGM CPU watch not active; samples may use slower live queries\n");
  if (dcgm_topology_build_sock_power_map() != 0)
    TRACE("DCGM CPU socket power mapping unavailable (sysfs packages vs DCGM_FE_CPU)\n");
  else if (dcgm_cpu_sock_watch_install() != 0)
    TRACE("DCGM CPU socket power field watch not active; using entity reads\n");
  g_dcgm_mono_prev_us = 0;
  g_dcgm_stat_seeded = 0;
  g_dcgm_ready = 1;
  g_dcgm_retry_after = 0;
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
  /* LIKWID PMCs unavailable/busy: use direct-MSR fallback path for collection. */
  likwid_pmc_adapter_finalize();
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
  stats_set(stats, "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_SCALAR_SINGLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE", 0);
  stats_set(stats, "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE", 0);
  stats_set(stats, "ARM_EST_FLOPS", 0);
  stats_set(stats, "ARM_DRAM_BW_BYTES", 0);
  stats_set(stats, "DCGM_CPU_POWER_UTIL_W", 0ULL);
  stats_set(stats, "DCGM_CPU_POWER_LIMIT_W", 0ULL);
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

  if (g_dcgm_ready) {
    if (clock_gettime(CLOCK_MONOTONIC, &t0) != 0) {
      t0.tv_sec = 0;
      t0.tv_nsec = 0;
    }
    update_rc = dcgmUpdateAllFields(g_dcgm_handle, 0);
    if (clock_gettime(CLOCK_MONOTONIC, &t1) == 0 && (t0.tv_sec > 0 || t0.tv_nsec > 0))
      update_elapsed_us = ((long long)t1.tv_sec - (long long)t0.tv_sec) * 1000000LL
	  + ((long long)t1.tv_nsec - (long long)t0.tv_nsec) / 1000LL;
    if (update_elapsed_us > 500000LL)
      monitor_log_warn("cpu_counter_metrics: dcgmUpdateAllFields slow path elapsed_us=%lld\n",
		       update_elapsed_us);
    if (update_rc != DCGM_ST_OK) {
      g_dcgm_update_failures++;
      monitor_log_warn(
	  "cpu_counter_metrics: dcgmUpdateAllFields failed rc=%d (failures=%lu); resetting DCGM backend\n",
	  (int)update_rc, g_dcgm_update_failures);
      dcgm_backend_cleanup();
      g_dcgm_retry_after = time(NULL) + 60;
    }
  }
  if (g_dcgm_ready && g_dcgm_ncpu_entities > 0)
    dcgm_cpu_refresh_socket_power();
  if (g_dcgm_ready && g_dcjm_cur != NULL && g_dcjm_prev != NULL && nr_cpus > 0)
    proc_stat_ok = (dcgm_proc_stat_read_cpus(g_dcjm_cur, nr_cpus) == 0);
  if (g_dcgm_ready) {
    struct timespec mono;

    if (clock_gettime(CLOCK_MONOTONIC, &mono) == 0) {
      long long mono_us_collect =
	  (long long) mono.tv_sec * 1000000LL + (long long) mono.tv_nsec / 1000LL;

      if (g_dcgm_mono_prev_us > 0 && mono_us_collect > g_dcgm_mono_prev_us)
	delta_us_collect = mono_us_collect - g_dcgm_mono_prev_us;
      g_dcgm_mono_prev_us = mono_us_collect;
    }
  }
#endif
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
      long long delta_us = delta_us_collect;
      int rd;

      memset(&sample, 0, sizeof(sample));
      rd = read_dcgm_cpu_sample(i, &sample);
      if (rd == 0)
	dcgm_cpu_scale_util_if_fraction(&sample);
      if ((rd != 0 || sample.util_total <= 0.0) && proc_stat_ok && g_dcgm_stat_seeded)
	dcgm_cpu_sample_from_jiffy_diff(&sample, &g_dcjm_cur[i], &g_dcjm_prev[i]);

      if (rd == 0 && sample.ts > 0) {
	if (g_dcgm_last_ts[i] > 0 && sample.ts > g_dcgm_last_ts[i]) {
	  long long dts = sample.ts - g_dcgm_last_ts[i];

	  if (dts > 0 && dts < 3600LL * 1000000LL)
	    delta_us = dts;
	}
	g_dcgm_last_ts[i] = sample.ts;
      }

      if (sample.clock_khz <= 0.0)
	sample.clock_khz = dcgm_cpu_nominal_freq_khz(i);

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
#ifdef MONITOR_CPU_BACKEND_DCGM
  if (g_dcgm_ready && proc_stat_ok && g_dcjm_prev != NULL && g_dcjm_cur != NULL && nr_cpus > 0) {
    memcpy(g_dcjm_prev, g_dcjm_cur, (size_t) nr_cpus * sizeof(*g_dcjm_prev));
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
  .st_name = "cpu_counter_metrics",
};
