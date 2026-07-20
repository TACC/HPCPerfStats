/* arm_aarch64_imc — ARM64 DRAM PMU counters via perf_event (DMC/IMC PMUs). */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/perf_event.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#include "stats.h"
#include "trace.h"
#include "arm_imc.h"

#define ARM_AARCH64_IMC_MAX_DEVS 32

struct arm_aarch64_imc_dev {
  char name[64];
  int cpu;
  int fd_read;
  int fd_write;
  uint64_t last_read;
  uint64_t last_write;
  uint64_t acc_read;
  uint64_t acc_write;
  int valid;
};

static struct arm_aarch64_imc_dev g_arm_aarch64_imc[ARM_AARCH64_IMC_MAX_DEVS];
static int g_arm_aarch64_imc_n = 0;

static void arm_aarch64_imc_cleanup(void)
{
  int i;

  for (i = 0; i < g_arm_aarch64_imc_n; i++) {
    if (g_arm_aarch64_imc[i].fd_read >= 0) {
      close(g_arm_aarch64_imc[i].fd_read);
      g_arm_aarch64_imc[i].fd_read = -1;
    }
    if (g_arm_aarch64_imc[i].fd_write >= 0) {
      close(g_arm_aarch64_imc[i].fd_write);
      g_arm_aarch64_imc[i].fd_write = -1;
    }
    memset(&g_arm_aarch64_imc[i], 0, sizeof(g_arm_aarch64_imc[i]));
    g_arm_aarch64_imc[i].fd_read = -1;
    g_arm_aarch64_imc[i].fd_write = -1;
  }
  g_arm_aarch64_imc_n = 0;
}

static long perf_event_open_wrap(struct perf_event_attr *attr, pid_t pid, int cpu, int group_fd,
                                 unsigned long flags)
{
  return syscall(__NR_perf_event_open, attr, pid, cpu, group_fd, flags);
}

static int read_u64_path(const char *path, uint64_t *v)
{
  int fd;
  char buf[64];
  ssize_t n;
  char *end = NULL;
  unsigned long long x;

  if (path == NULL || v == NULL)
    return -1;
  fd = open(path, O_RDONLY);
  if (fd < 0)
    return -1;
  n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0)
    return -1;
  buf[n] = '\0';
  x = strtoull(buf, &end, 0);
  if (end == buf)
    return -1;
  *v = (uint64_t)x;
  return 0;
}

static int first_cpu_from_cpumask(const char *path)
{
  int fd, idx = 0, i;
  char buf[256];
  ssize_t n;

  if (path == NULL)
    return -1;
  fd = open(path, O_RDONLY);
  if (fd < 0)
    return -1;
  n = read(fd, buf, sizeof(buf) - 1);
  close(fd);
  if (n <= 0)
    return -1;
  buf[n] = '\0';
  for (i = 0; i < (int)n; i++) {
    char c = buf[i];
    int val = -1, b;

    if (c >= '0' && c <= '9')
      val = c - '0';
    else if (c >= 'a' && c <= 'f')
      val = 10 + (c - 'a');
    else if (c >= 'A' && c <= 'F')
      val = 10 + (c - 'A');
    else
      continue;
    for (b = 0; b < 4; b++) {
      if (val & (1 << b))
        return idx + b;
    }
    idx += 4;
  }
  return -1;
}

static int parse_field_bits(const char *s, int *lo, int *hi)
{
  char *colon;
  char *dash;

  if (s == NULL || lo == NULL || hi == NULL)
    return -1;
  colon = strchr((char *)s, ':');
  if (colon == NULL)
    return -1;
  colon++;
  dash = strchr(colon, '-');
  if (dash != NULL) {
    *lo = atoi(colon);
    *hi = atoi(dash + 1);
  } else {
    *lo = atoi(colon);
    *hi = *lo;
  }
  return (*lo >= 0 && *hi >= *lo) ? 0 : -1;
}

static void set_bits_u64(unsigned long long *dst, int lo, int hi, unsigned long long value)
{
  int width = hi - lo + 1;
  uint64_t mask;

  if (width >= 64)
    mask = ~0ULL;
  else
    mask = ((1ULL << width) - 1ULL);
  *dst &= ~(mask << lo);
  *dst |= (value & mask) << lo;
}

static int apply_event_token(const char *pmu_dir, struct perf_event_attr *attr, const char *key,
                             uint64_t value)
{
  char path[512];
  char fmt[128];
  int lo, hi, fd;
  ssize_t n;

  if (pmu_dir == NULL || attr == NULL || key == NULL)
    return -1;
  if (snprintf(path, sizeof(path), "%s/format/%s", pmu_dir, key) >= (int)sizeof(path))
    return -1;
  fd = open(path, O_RDONLY);
  if (fd < 0)
    return -1;
  n = read(fd, fmt, sizeof(fmt) - 1);
  close(fd);
  if (n <= 0)
    return -1;
  fmt[n] = '\0';
  if (parse_field_bits(fmt, &lo, &hi) != 0)
    return -1;
  if (strncmp(fmt, "config1:", 8) == 0)
    set_bits_u64(&attr->config1, lo, hi, value);
  else if (strncmp(fmt, "config2:", 8) == 0)
    set_bits_u64(&attr->config2, lo, hi, value);
  else
    set_bits_u64(&attr->config, lo, hi, value);
  return 0;
}

static int event_attr_from_alias(const char *pmu_dir, const char *alias,
                                 struct perf_event_attr *attr)
{
  char path[512];
  char expr[256];
  char *tok;
  char *save = NULL;
  int fd;
  ssize_t n;
  uint64_t type = 0;

  if (pmu_dir == NULL || alias == NULL || attr == NULL)
    return -1;

  memset(attr, 0, sizeof(*attr));
  attr->size = sizeof(*attr);
  attr->disabled = 0;
  attr->exclude_kernel = 0;
  attr->exclude_hv = 0;
  attr->exclude_idle = 0;
  attr->inherit = 0;
  attr->read_format = 0;

  if (snprintf(path, sizeof(path), "%s/type", pmu_dir) >= (int)sizeof(path))
    return -1;
  if (read_u64_path(path, &type) != 0)
    return -1;
  attr->type = (uint32_t)type;

  if (snprintf(path, sizeof(path), "%s/events/%s", pmu_dir, alias) >= (int)sizeof(path))
    return -1;
  fd = open(path, O_RDONLY);
  if (fd < 0)
    return -1;
  n = read(fd, expr, sizeof(expr) - 1);
  close(fd);
  if (n <= 0)
    return -1;
  expr[n] = '\0';

  tok = strtok_r(expr, ",\n\r\t ", &save);
  while (tok != NULL) {
    char *eq = strchr(tok, '=');

    if (eq != NULL) {
      uint64_t val;

      *eq = '\0';
      val = strtoull(eq + 1, NULL, 0);
      if (apply_event_token(pmu_dir, attr, tok, val) != 0)
        return -1;
    }
    tok = strtok_r(NULL, ",\n\r\t ", &save);
  }
  return 0;
}

static int open_arm_aarch64_imc_counter(const char *pmu_dir, const char *cpumask_path,
                                        const char **aliases)
{
  int cpu, i, fd;
  struct perf_event_attr attr;

  if (pmu_dir == NULL || cpumask_path == NULL || aliases == NULL)
    return -1;
  cpu = first_cpu_from_cpumask(cpumask_path);
  if (cpu < 0)
    return -1;
  for (i = 0; aliases[i] != NULL; i++) {
    if (event_attr_from_alias(pmu_dir, aliases[i], &attr) != 0)
      continue;
    fd = (int)perf_event_open_wrap(&attr, -1, cpu, -1, 0);
    if (fd >= 0)
      return fd;
  }
  return -1;
}

static int arm_aarch64_imc_probe_pmu(const char *pmu_name)
{
  if (pmu_name == NULL)
    return 0;
  return (strstr(pmu_name, "dmc") != NULL || strstr(pmu_name, "imc") != NULL);
}

static int arm_aarch64_imc_register_pmu(const char *pmu_name)
{
  char pmu_dir[512];
  char cpumask[512];
  int fd_r;
  int fd_w;
  struct arm_aarch64_imc_dev *dev;
  const char *read_aliases[] = {"cas_count_read", "read_cas", "reads", "read_bytes", NULL};
  const char *write_aliases[] = {"cas_count_write", "write_cas", "writes", "write_bytes", NULL};

  if (g_arm_aarch64_imc_n >= ARM_AARCH64_IMC_MAX_DEVS)
    return 0;
  if (snprintf(pmu_dir, sizeof(pmu_dir), "/sys/bus/event_source/devices/%s", pmu_name) >=
      (int)sizeof(pmu_dir))
    return 0;
  if (snprintf(cpumask, sizeof(cpumask), "%s/cpumask", pmu_dir) >= (int)sizeof(cpumask))
    return 0;

  fd_r = open_arm_aarch64_imc_counter(pmu_dir, cpumask, read_aliases);
  fd_w = open_arm_aarch64_imc_counter(pmu_dir, cpumask, write_aliases);
  if (fd_r < 0 || fd_w < 0) {
    if (fd_r >= 0)
      close(fd_r);
    if (fd_w >= 0)
      close(fd_w);
    return 0;
  }

  dev = &g_arm_aarch64_imc[g_arm_aarch64_imc_n++];
  memset(dev, 0, sizeof(*dev));
  {
    size_t name_len = strnlen(pmu_name, sizeof(dev->name) - 1);

    memcpy(dev->name, pmu_name, name_len);
    dev->name[name_len] = '\0';
  }
  dev->fd_read = fd_r;
  dev->fd_write = fd_w;
  dev->cpu = first_cpu_from_cpumask(cpumask);
  dev->valid = 1;
  return 1;
}

static int arm_aarch64_imc_begin(struct stats_type *type)
{
  DIR *d;
  struct dirent *de;

  if (type == NULL)
    return -1;
  arm_aarch64_imc_cleanup();
  d = opendir("/sys/bus/event_source/devices");
  if (d == NULL) {
    type->st_enabled = 0;
    return 0;
  }
  while ((de = readdir(d)) != NULL && g_arm_aarch64_imc_n < ARM_AARCH64_IMC_MAX_DEVS) {
    if (de->d_name[0] == '.')
      continue;
    if (!arm_aarch64_imc_probe_pmu(de->d_name))
      continue;
    (void)arm_aarch64_imc_register_pmu(de->d_name);
  }
  closedir(d);
  if (g_arm_aarch64_imc_n == 0)
    type->st_enabled = 0;
  return 0;
}

static void arm_aarch64_imc_collect(struct stats_type *type)
{
  int i;

  if (type == NULL)
    return;
  for (i = 0; i < g_arm_aarch64_imc_n; i++) {
    uint64_t cur_r = 0;
    uint64_t cur_w = 0;
    uint64_t d_r = 0;
    uint64_t d_w = 0;
    struct arm_aarch64_imc_dev *dev = &g_arm_aarch64_imc[i];
    struct stats *s;

    if (!dev->valid)
      continue;
    if (read(dev->fd_read, &cur_r, sizeof(cur_r)) != (ssize_t)sizeof(cur_r))
      continue;
    if (read(dev->fd_write, &cur_w, sizeof(cur_w)) != (ssize_t)sizeof(cur_w))
      continue;
    if (dev->last_read > 0 && cur_r >= dev->last_read)
      d_r = cur_r - dev->last_read;
    if (dev->last_write > 0 && cur_w >= dev->last_write)
      d_w = cur_w - dev->last_write;
    dev->last_read = cur_r;
    dev->last_write = cur_w;
    dev->acc_read += (d_r / 64ULL);
    dev->acc_write += (d_w / 64ULL);
    s = get_current_stats(type, dev->name);
    if (s == NULL)
      continue;
    stats_set(s, "dram_cas_reads", dev->acc_read);
    stats_set(s, "dram_cas_writes", dev->acc_write);
  }
}

struct stats_type arm_imc_stats_type = {
    .st_begin = &arm_aarch64_imc_begin,
    .st_collect = &arm_aarch64_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(ARM_AARCH64_IMC_KEYS),
#undef X
    .st_name = ARM_AARCH64_IMC_ST_NAME,
};
