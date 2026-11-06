/* Pure util / clock helpers for the `arm` host_cpu_hw backend (no DCGM, no stats emit). */
#include <ctype.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cpu_counter_metrics_arm_util.h"

#define ARM_FREQ_PATH_MAX 256
#define ARM_FREQ_BUF_MAX 40

double arm_clamp_percent(double v)
{
  if (v < 0.0)
    return 0.0;
  if (v > 100.0)
    return 100.0;
  return v;
}

static unsigned long long arm_jifs_total(const struct arm_cpu_jifs *j)
{
  return j->u + j->nice + j->sys + j->idle + j->iow + j->irq + j->sft + j->stl + j->gu + j->gn;
}

static unsigned long long arm_jifs_nid(const struct arm_cpu_jifs *j)
{
  return j->u + j->nice + j->sys + j->irq + j->sft + j->stl + j->gu + j->gn;
}

static unsigned long long arm_jifs_delta(unsigned long long cur, unsigned long long prev)
{
  return (cur >= prev) ? (cur - prev) : 0ULL;
}

void arm_cpu_sample_from_jiffy_diff(struct arm_cpu_sample *s, const struct arm_cpu_jifs *cur,
                                    const struct arm_cpu_jifs *prev)
{
  unsigned long long pt, ct, pn, cn, d_tot;

  if (s == NULL || cur == NULL || prev == NULL)
    return;
  pt = arm_jifs_total(prev);
  ct = arm_jifs_total(cur);
  pn = arm_jifs_nid(prev);
  cn = arm_jifs_nid(cur);
  if (ct < pt || cn < pn)
    return;
  d_tot = ct - pt;
  if (d_tot == 0)
    return;
  s->util_total = arm_clamp_percent(100.0 * (double)(cn - pn) / (double)d_tot);
  s->util_user = arm_clamp_percent(
      100.0 * (double)(arm_jifs_delta(cur->u, prev->u) + arm_jifs_delta(cur->nice, prev->nice)) /
      (double)d_tot);
  s->util_sys =
      arm_clamp_percent(100.0 * (double)arm_jifs_delta(cur->sys, prev->sys) / (double)d_tot);
  s->util_irq = arm_clamp_percent(
      100.0 * (double)(arm_jifs_delta(cur->irq, prev->irq) + arm_jifs_delta(cur->sft, prev->sft)) /
      (double)d_tot);
  s->util_nice = 0.0;
}

int arm_proc_stat_parse_cpu_line(const char *line, struct arm_cpu_jifs *out)
{
  const char *p = line;
  char *end = NULL;
  long idcpu;
  struct arm_cpu_jifs j;

  if (line == NULL || out == NULL || strncmp(p, "cpu", 3) != 0)
    return -1;
  p += 3;
  if (!isdigit((unsigned char)*p))
    return -1;
  idcpu = strtol(p, &end, 10);
  if (idcpu < 0 || idcpu > 1000000L)
    return -1;
  memset(&j, 0, sizeof(j));
  if (sscanf(end, "%llu %llu %llu %llu %llu %llu %llu %llu %llu %llu", &j.u, &j.nice, &j.sys,
             &j.idle, &j.iow, &j.irq, &j.sft, &j.stl, &j.gu, &j.gn) < 4)
    return -1;
  *out = j;
  return (int)idcpu;
}

int arm_cpu_freq_open(const char *root, int cpu)
{
  char path[ARM_FREQ_PATH_MAX];
  int n;

  if (root == NULL || cpu < 0)
    return -1;
  n = snprintf(path, sizeof(path), "%s/cpu%d/cpufreq/scaling_cur_freq", root, cpu);
  if (n < 0 || (size_t)n >= sizeof(path))
    return -1;
  return open(path, O_RDONLY | O_CLOEXEC);
}

double arm_cpu_freq_read_khz(int fd)
{
  char buf[ARM_FREQ_BUF_MAX];
  ssize_t n;
  long long khz = 0;

  if (fd < 0)
    return 0.0;
  n = pread(fd, buf, sizeof(buf) - 1, 0);
  if (n <= 0)
    return 0.0;
  buf[n] = '\0';
  if (sscanf(buf, "%lld", &khz) != 1 || khz <= 0)
    return 0.0;
  return (double)khz;
}

static double arm_cpu_read_khz_file(const char *root, int cpu, const char *name)
{
  char path[ARM_FREQ_PATH_MAX];
  int fd, n;
  double khz;

  n = snprintf(path, sizeof(path), "%s/cpu%d/cpufreq/%s", root, cpu, name);
  if (n < 0 || (size_t)n >= sizeof(path))
    return 0.0;
  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0)
    return 0.0;
  khz = arm_cpu_freq_read_khz(fd);
  close(fd);
  return khz;
}

double arm_cpu_nominal_freq_khz(const char *root, int cpu)
{
  static const char *const chain[] = {"base_frequency", "cpuinfo_max_freq", "scaling_max_freq",
                                      "scaling_cur_freq"};
  size_t k;

  if (root == NULL || cpu < 0)
    return 0.0;
  for (k = 0; k < sizeof(chain) / sizeof(chain[0]); k++) {
    double khz = arm_cpu_read_khz_file(root, cpu, chain[k]);

    if (khz > 0.0)
      return khz;
  }
  return (cpu != 0) ? arm_cpu_nominal_freq_khz(root, 0) : 0.0;
}
