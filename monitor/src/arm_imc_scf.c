/* arm_imc_scf — Grace SCF uncore DRAM traffic via direct perf (pure; no stats, no LIKWID). */
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "arm_imc_scf.h"
#include "monitor_log.h"

#define ARM_IMC_SCF_RD_EVENT "cmem_rd_data"
#define ARM_IMC_SCF_WR_EVENT "cmem_wr_total_bytes"

int arm_imc_scf_begin_mode(int n_scf, int n_perf)
{
  if (n_scf > 0)
    return ARM_IMC_MODE_SCF;
  return (n_perf > 0) ? ARM_IMC_MODE_PERF : ARM_IMC_MODE_RESCAN;
}

void arm_imc_scf_init(struct arm_imc_scf *s, const char *devices_dir, arm_perf_open_fn open_fn)
{
  int i;

  if (s == NULL)
    return;
  memset(s, 0, sizeof(*s));
  s->devices_dir = devices_dir;
  s->open_fn = (open_fn != NULL) ? open_fn : arm_perf_open_cpu;
  for (i = 0; i < ARM_IMC_SCF_MAX_SOCKETS; i++) {
    s->dev[i].fd_rd = -1;
    s->dev[i].fd_wr = -1;
  }
}

/* <N> of "nvidia_scf_pmu_<N>", or -1 for other names / N out of range. */
static int scf_socket_of(const char *name)
{
  size_t plen = strlen(ARM_IMC_SCF_PMU_PREFIX);
  char *end = NULL;
  long sock;

  if (strncmp(name, ARM_IMC_SCF_PMU_PREFIX, plen) != 0)
    return -1;
  sock = strtol(name + plen, &end, 10);
  if (end == name + plen || *end != '\0' || sock < 0 || sock >= ARM_IMC_SCF_MAX_SOCKETS)
    return -1;
  return (int)sock;
}

/* Bit per socket <N> present under @p devices_dir. */
static unsigned int scf_socket_bits(const char *devices_dir)
{
  unsigned int bits = 0;
  struct dirent *de;
  DIR *d;

  if (devices_dir == NULL || (d = opendir(devices_dir)) == NULL)
    return 0;
  while ((de = readdir(d)) != NULL) {
    int sock = scf_socket_of(de->d_name);

    if (sock >= 0)
      bits |= 1U << sock;
  }
  closedir(d);
  return bits;
}

int arm_imc_scf_count_pmus(const char *devices_dir)
{
  unsigned int bits = scf_socket_bits(devices_dir);
  int n = 0;

  for (; bits != 0; bits &= bits - 1)
    n++;
  return n;
}

/* Opens both events of socket @p sock into @p d. @return 0, or -1 (nothing left open). */
static int scf_open_socket(struct arm_imc_scf *s, int sock, struct arm_imc_scf_dev *d)
{
  char pmu_dir[512];
  char path[600];
  struct perf_event_attr rd, wr;
  const char *why = "events/" ARM_IMC_SCF_RD_EVENT " or " ARM_IMC_SCF_WR_EVENT " unreadable";

  memset(d, 0, sizeof(*d));
  d->socket = sock;
  d->fd_rd = d->fd_wr = -1;
  snprintf(pmu_dir, sizeof(pmu_dir), "%s/" ARM_IMC_SCF_PMU_PREFIX "%d", s->devices_dir, sock);
  snprintf(path, sizeof(path), "%s/cpumask", pmu_dir);
  d->cpu = arm_perf_cpumask_first(path);
  if (d->cpu < 0)
    why = "cpumask unreadable";
  else if (arm_perf_attr_from_sysfs(pmu_dir, ARM_IMC_SCF_RD_EVENT, &rd) == 0 &&
           arm_perf_attr_from_sysfs(pmu_dir, ARM_IMC_SCF_WR_EVENT, &wr) == 0) {
    errno = 0;
    d->fd_rd = s->open_fn(&rd, d->cpu);
    d->fd_wr = (d->fd_rd >= 0) ? s->open_fn(&wr, d->cpu) : -1;
    if (d->fd_rd >= 0 && d->fd_wr >= 0)
      return 0;
    why = strerror(errno != 0 ? errno : EINVAL);
  }
  if (d->fd_rd >= 0)
    close(d->fd_rd);
  d->fd_rd = d->fd_wr = -1;
  if (!(s->fail_logged & (1U << sock)))
    monitor_log_warn("arm_aarch64_imc: " ARM_IMC_SCF_PMU_PREFIX "%d on CPU %d not opened (%s); "
                     "socket %d has no SCF rows\n",
                     sock, d->cpu, why, sock);
  s->fail_logged |= 1U << sock;
  return -1;
}

int arm_imc_scf_attach(struct arm_imc_scf *s)
{
  unsigned int bits;
  int sock;

  if (s == NULL || s->devices_dir == NULL)
    return 0;
  arm_imc_scf_close(s);
  bits = scf_socket_bits(s->devices_dir);
  for (sock = 0; sock < ARM_IMC_SCF_MAX_SOCKETS; sock++) {
    if ((bits & (1U << sock)) && scf_open_socket(s, sock, &s->dev[s->n]) == 0)
      s->n++;
  }
  return s->n;
}

int arm_imc_scf_tick(struct arm_imc_scf *s)
{
  if (s == NULL || s->n > 0)
    return (s != NULL) ? s->n : 0;
  if (++s->ticks_since_scan < ARM_IMC_SCF_RESCAN_TICKS)
    return 0;
  s->ticks_since_scan = 0;
  return (arm_imc_scf_count_pmus(s->devices_dir) > 0) ? arm_imc_scf_attach(s) : 0;
}

int arm_imc_scf_read(struct arm_imc_scf_dev *d, unsigned long long *cas_rd,
                     unsigned long long *cas_wr)
{
  double rd = 0.0, wr = 0.0;

  if (d == NULL || cas_rd == NULL || cas_wr == NULL)
    return -1;
  if (arm_perf_read_scaled(d->fd_rd, &d->prev_rd, &rd) == 0 && rd > 0.0)
    d->life_rd_beats += (unsigned long long)(rd + 0.5);
  if (arm_perf_read_scaled(d->fd_wr, &d->prev_wr, &wr) == 0 && wr > 0.0)
    d->life_wr_bytes += (unsigned long long)(wr + 0.5);
  *cas_rd = arm_imc_scf_rd_beats_to_cas(d->life_rd_beats);
  *cas_wr = arm_imc_scf_wr_bytes_to_cas(d->life_wr_bytes);
  return 0;
}

void arm_imc_scf_close(struct arm_imc_scf *s)
{
  int i;

  if (s == NULL)
    return;
  for (i = 0; i < s->n; i++) {
    if (s->dev[i].fd_rd >= 0)
      close(s->dev[i].fd_rd);
    if (s->dev[i].fd_wr >= 0)
      close(s->dev[i].fd_wr);
    s->dev[i].fd_rd = s->dev[i].fd_wr = -1;
  }
  s->n = 0;
}

unsigned long long arm_imc_scf_rd_beats_to_cas(unsigned long long beats)
{
  return beats / 2ULL;
}

unsigned long long arm_imc_scf_wr_bytes_to_cas(unsigned long long bytes)
{
  return bytes / 64ULL;
}
