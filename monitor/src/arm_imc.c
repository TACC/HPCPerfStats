/* arm_aarch64_imc — ARM64 DRAM CAS counters via direct perf_event.
 *
 * Grace: one `scf<N>` device per nvidia_scf_pmu_<N> (arm_imc_scf.c), cmem_rd_data /
 * cmem_wr_total_bytes read scaled. Other aarch64: perf PMUs whose name contains dmc/imc.
 * When neither exists at begin the type stays enabled with 0 devices and re-scans every
 * ARM_IMC_SCF_RESCAN_TICKS ticks (nvidia_cspmu can bind after the daemon starts). */
#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "stats.h"
#include "trace.h"
#include "arm_imc.h"
#include "arm_imc_scf.h"
#include "arm_perf_util.h"
#include "monitor_log.h"

#define ARM_AARCH64_IMC_MAX_DEVS 32

struct arm_aarch64_imc_dev {
  char name[64];
  int fd_read;
  int fd_write;
  struct arm_perf_prev prev_read;
  struct arm_perf_prev prev_write;
  unsigned long long acc_read;
  unsigned long long acc_write;
};

static struct arm_aarch64_imc_dev g_arm_aarch64_imc[ARM_AARCH64_IMC_MAX_DEVS];
static int g_arm_aarch64_imc_n = 0;
static int g_arm_aarch64_imc_mode; /* enum arm_imc_mode */
static struct arm_imc_scf g_arm_aarch64_imc_scf;

static void arm_aarch64_imc_cleanup(void)
{
  int i;

  for (i = 0; i < g_arm_aarch64_imc_n; i++) {
    if (g_arm_aarch64_imc[i].fd_read >= 0)
      close(g_arm_aarch64_imc[i].fd_read);
    if (g_arm_aarch64_imc[i].fd_write >= 0)
      close(g_arm_aarch64_imc[i].fd_write);
  }
  memset(g_arm_aarch64_imc, 0, sizeof(g_arm_aarch64_imc));
  g_arm_aarch64_imc_n = 0;
  arm_imc_scf_close(&g_arm_aarch64_imc_scf);
  g_arm_aarch64_imc_mode = ARM_IMC_MODE_RESCAN;
}

/* First alias that resolves and opens on the PMU's cpumask CPU; -1 when none. */
static int open_arm_aarch64_imc_counter(const char *pmu_dir, const char *const *aliases)
{
  char cpumask[600];
  struct perf_event_attr attr;
  int cpu, i, fd;

  snprintf(cpumask, sizeof(cpumask), "%s/cpumask", pmu_dir);
  cpu = arm_perf_cpumask_first(cpumask);
  if (cpu < 0)
    return -1;
  for (i = 0; aliases[i] != NULL; i++) {
    if (arm_perf_attr_from_sysfs(pmu_dir, aliases[i], &attr) != 0)
      continue;
    fd = arm_perf_open_cpu(&attr, cpu);
    if (fd >= 0)
      return fd;
  }
  return -1;
}

static int arm_aarch64_imc_probe_pmu(const char *pmu_name)
{
  return (strstr(pmu_name, "dmc") != NULL || strstr(pmu_name, "imc") != NULL);
}

static void arm_aarch64_imc_register_pmu(const char *pmu_name)
{
  static const char *const read_aliases[] = {"cas_count_read", "read_cas", "reads", "read_bytes",
                                             NULL};
  static const char *const write_aliases[] = {"cas_count_write", "write_cas", "writes",
                                              "write_bytes", NULL};
  char pmu_dir[512];
  struct arm_aarch64_imc_dev *dev;
  int fd_r, fd_w;

  if (snprintf(pmu_dir, sizeof(pmu_dir), "%s/%s", ARM_PERF_DEVICES_DIR, pmu_name) >=
      (int)sizeof(pmu_dir))
    return;
  fd_r = open_arm_aarch64_imc_counter(pmu_dir, read_aliases);
  fd_w = open_arm_aarch64_imc_counter(pmu_dir, write_aliases);
  if (fd_r < 0 || fd_w < 0) {
    if (fd_r >= 0)
      close(fd_r);
    if (fd_w >= 0)
      close(fd_w);
    return;
  }
  dev = &g_arm_aarch64_imc[g_arm_aarch64_imc_n++];
  memset(dev, 0, sizeof(*dev));
  snprintf(dev->name, sizeof(dev->name), "%s", pmu_name);
  dev->fd_read = fd_r;
  dev->fd_write = fd_w;
}

/* Non-Grace fallback: every perf PMU named *dmc* / *imc*. */
static void arm_aarch64_imc_scan_dmc(void)
{
  DIR *d = opendir(ARM_PERF_DEVICES_DIR);
  struct dirent *de;

  while (d != NULL && (de = readdir(d)) != NULL && g_arm_aarch64_imc_n < ARM_AARCH64_IMC_MAX_DEVS) {
    if (de->d_name[0] != '.' && arm_aarch64_imc_probe_pmu(de->d_name))
      arm_aarch64_imc_register_pmu(de->d_name);
  }
  if (d != NULL)
    closedir(d);
}

static int arm_aarch64_imc_begin(struct stats_type *type)
{
  if (type == NULL)
    return -1;
  arm_aarch64_imc_cleanup();
  arm_imc_scf_init(&g_arm_aarch64_imc_scf, ARM_PERF_DEVICES_DIR, NULL);
  if (arm_imc_scf_attach(&g_arm_aarch64_imc_scf) == 0)
    arm_aarch64_imc_scan_dmc();
  g_arm_aarch64_imc_mode = arm_imc_scf_begin_mode(g_arm_aarch64_imc_scf.n, g_arm_aarch64_imc_n);
  if (g_arm_aarch64_imc_mode == ARM_IMC_MODE_SCF)
    monitor_log_info("arm_aarch64_imc: Grace SCF DRAM CAS via direct perf on %d socket(s)\n",
                     g_arm_aarch64_imc_scf.n);
  else if (g_arm_aarch64_imc_mode == ARM_IMC_MODE_RESCAN)
    monitor_log_info("arm_aarch64_imc: no nvidia_scf_pmu: check firmware APMT / nvidia_cspmu "
                     "binding (no dmc/imc PMU either; re-scanning every %d ticks)\n",
                     ARM_IMC_SCF_RESCAN_TICKS);
  return 0;
}

static void arm_aarch64_imc_collect_scf(struct stats_type *type)
{
  int i;

  for (i = 0; i < g_arm_aarch64_imc_scf.n; i++) {
    struct arm_imc_scf_dev *dev = &g_arm_aarch64_imc_scf.dev[i];
    unsigned long long rd = 0, wr = 0;
    char name[16];
    struct stats *s;

    if (arm_imc_scf_read(dev, &rd, &wr) != 0)
      continue;
    snprintf(name, sizeof(name), "scf%d", dev->socket);
    s = get_current_stats(type, name);
    if (s == NULL)
      continue;
    stats_set(s, "dram_cas_reads", rd);
    stats_set(s, "dram_cas_writes", wr);
  }
}

static void arm_aarch64_imc_collect_dmc(struct stats_type *type)
{
  int i;

  for (i = 0; i < g_arm_aarch64_imc_n; i++) {
    struct arm_aarch64_imc_dev *dev = &g_arm_aarch64_imc[i];
    double d_r = 0.0, d_w = 0.0;
    struct stats *s;

    if (arm_perf_read_scaled(dev->fd_read, &dev->prev_read, &d_r) != 0 ||
        arm_perf_read_scaled(dev->fd_write, &dev->prev_write, &d_w) != 0)
      continue;
    dev->acc_read += (unsigned long long)(d_r + 0.5) / 64ULL;
    dev->acc_write += (unsigned long long)(d_w + 0.5) / 64ULL;
    s = get_current_stats(type, dev->name);
    if (s == NULL)
      continue;
    stats_set(s, "dram_cas_reads", dev->acc_read);
    stats_set(s, "dram_cas_writes", dev->acc_write);
  }
}

static void arm_aarch64_imc_collect(struct stats_type *type)
{
  if (type == NULL)
    return;
  if (g_arm_aarch64_imc_mode == ARM_IMC_MODE_RESCAN) {
    if (arm_imc_scf_tick(&g_arm_aarch64_imc_scf) == 0)
      return;
    g_arm_aarch64_imc_mode = ARM_IMC_MODE_SCF;
    monitor_log_info("arm_aarch64_imc: nvidia_scf_pmu appeared; SCF DRAM CAS on %d socket(s)\n",
                     g_arm_aarch64_imc_scf.n);
  }
  if (g_arm_aarch64_imc_mode == ARM_IMC_MODE_SCF)
    arm_aarch64_imc_collect_scf(type);
  else
    arm_aarch64_imc_collect_dmc(type);
}

struct stats_type arm_imc_stats_type = {
    .st_begin = &arm_aarch64_imc_begin,
    .st_collect = &arm_aarch64_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(ARM_AARCH64_IMC_KEYS),
#undef X
    .st_name = ARM_AARCH64_IMC_ST_NAME,
};
