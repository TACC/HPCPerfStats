/* arm_perf_util — shared direct perf_event helpers (open, sysfs PMU parse, scaled read). */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#include "arm_perf_util.h"

#ifndef PERF_FLAG_FD_CLOEXEC
#define PERF_FLAG_FD_CLOEXEC (1UL << 3)
#endif

#define ARM_PERF_READ_FORMAT (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING)

int arm_perf_open_cpu(const struct perf_event_attr *attr, int cpu)
{
  if (attr == NULL || cpu < 0) {
    errno = EINVAL;
    return -1;
  }
  return (int)syscall(__NR_perf_event_open, attr, (pid_t)-1, cpu, -1, PERF_FLAG_FD_CLOEXEC);
}

void arm_perf_attr_raw(struct perf_event_attr *attr, uint32_t type, uint64_t config)
{
  if (attr == NULL)
    return;
  memset(attr, 0, sizeof(*attr));
  attr->size = sizeof(*attr);
  attr->type = type;
  attr->config = config;
  attr->read_format = ARM_PERF_READ_FORMAT;
}

/* Reads a small sysfs file into @p buf (NUL-terminated); -1 when missing or empty. */
static int read_small_file(const char *path, char *buf, size_t cap)
{
  int fd;
  ssize_t n;

  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0)
    return -1;
  n = read(fd, buf, cap - 1);
  close(fd);
  if (n <= 0)
    return -1;
  buf[n] = '\0';
  return 0;
}

/* "config:0-15" / "config1:3" -> field bits; -1 when unparsable. */
static int parse_format_bits(const char *fmt, int *lo, int *hi)
{
  const char *colon = strchr(fmt, ':');
  char *end = NULL;

  if (colon == NULL)
    return -1;
  *lo = (int)strtol(colon + 1, &end, 10);
  if (end == colon + 1)
    return -1;
  *hi = (*end == '-') ? (int)strtol(end + 1, NULL, 10) : *lo;
  return (*lo >= 0 && *hi >= *lo && *hi < 64) ? 0 : -1;
}

static void set_bits_u64(__u64 *dst, int lo, int hi, uint64_t value)
{
  int width = hi - lo + 1;
  uint64_t mask = (width >= 64) ? ~0ULL : ((1ULL << width) - 1ULL);

  *dst &= ~(mask << lo);
  *dst |= (value & mask) << lo;
}

/* Places one events/<name> term (e.g. event=0x1a5) by `<pmu_dir>/format/<term>`. */
static int apply_event_term(const char *pmu_dir, struct perf_event_attr *attr, const char *term,
                            uint64_t value)
{
  char path[512];
  char fmt[128];
  int lo, hi;

  if (snprintf(path, sizeof(path), "%s/format/%s", pmu_dir, term) >= (int)sizeof(path))
    return -1;
  if (read_small_file(path, fmt, sizeof(fmt)) != 0 || parse_format_bits(fmt, &lo, &hi) != 0)
    return -1;
  if (strncmp(fmt, "config1:", 8) == 0)
    set_bits_u64(&attr->config1, lo, hi, value);
  else if (strncmp(fmt, "config2:", 8) == 0)
    set_bits_u64(&attr->config2, lo, hi, value);
  else
    set_bits_u64(&attr->config, lo, hi, value);
  return 0;
}

int arm_perf_pmu_type(const char *pmu_dir, uint32_t *type)
{
  char path[512];
  char buf[64];
  char *end = NULL;
  unsigned long v;

  if (pmu_dir == NULL || type == NULL)
    return -1;
  if (snprintf(path, sizeof(path), "%s/type", pmu_dir) >= (int)sizeof(path) ||
      read_small_file(path, buf, sizeof(buf)) != 0)
    return -1;
  v = strtoul(buf, &end, 0);
  if (end == buf)
    return -1;
  *type = (uint32_t)v;
  return 0;
}

int arm_perf_attr_from_sysfs(const char *pmu_dir, const char *event, struct perf_event_attr *attr)
{
  char path[512];
  char buf[256];
  char *tok;
  char *save = NULL;
  uint32_t type;

  if (event == NULL || attr == NULL || arm_perf_pmu_type(pmu_dir, &type) != 0)
    return -1;
  arm_perf_attr_raw(attr, type, 0);
  if (snprintf(path, sizeof(path), "%s/events/%s", pmu_dir, event) >= (int)sizeof(path) ||
      read_small_file(path, buf, sizeof(buf)) != 0)
    return -1;
  for (tok = strtok_r(buf, ",\n\r\t ", &save); tok != NULL;
       tok = strtok_r(NULL, ",\n\r\t ", &save)) {
    char *eq = strchr(tok, '=');

    if (eq == NULL)
      continue;
    *eq = '\0';
    if (apply_event_term(pmu_dir, attr, tok, strtoull(eq + 1, NULL, 0)) != 0)
      return -1;
  }
  return 0;
}

int arm_perf_cpumask_first(const char *path)
{
  char buf[256];
  char *end = NULL;
  long cpu;

  if (path == NULL || read_small_file(path, buf, sizeof(buf)) != 0)
    return -1;
  cpu = strtol(buf, &end, 10);
  if (end == buf || cpu < 0 || cpu > (1L << 20))
    return -1;
  return (int)cpu;
}

void arm_perf_scale_sample(struct arm_perf_prev *prev, const uint64_t sample[3], double *delta_out)
{
  int reseed;

  if (delta_out != NULL)
    *delta_out = 0.0;
  if (prev == NULL || sample == NULL || delta_out == NULL)
    return;
  reseed = !prev->seeded || sample[0] < prev->value || sample[1] < prev->enabled ||
           sample[2] < prev->running;
  if (!reseed && sample[2] > prev->running)
    *delta_out = (double)(sample[0] - prev->value) * (double)(sample[1] - prev->enabled) /
                 (double)(sample[2] - prev->running);
  prev->value = sample[0];
  prev->enabled = sample[1];
  prev->running = sample[2];
  prev->seeded = 1;
}

int arm_perf_read_scaled(int fd, struct arm_perf_prev *prev, double *delta_out)
{
  uint64_t sample[3];

  if (delta_out != NULL)
    *delta_out = 0.0;
  if (fd < 0 || prev == NULL || delta_out == NULL)
    return -1;
  if (read(fd, sample, sizeof(sample)) != (ssize_t)sizeof(sample))
    return -1;
  arm_perf_scale_sample(prev, sample, delta_out);
  return 0;
}
