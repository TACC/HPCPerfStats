/*! \file x86_rapl.c
 *  x86 RAPL energy per socket from the perf `power` PMU, powercap energy_uj as fallback.
 *  Opens happen in begin; collect is one bounded read per open domain.
 */

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "monitor_log.h"
#include "x86_rapl.h"

#define X86_RAPL_WIRE_MASK ((1ULL << X86_RAPL_WIRE_BITS) - 1ULL)

static const char *const k_event[X86_RAPL_DOMAINS] = {"energy-pkg", "energy-cores", "energy-gpu",
                                                      "energy-ram"};
static const char *const k_core_zones[] = {"core", "cores", NULL};
static const char *const k_gpu_zones[] = {"uncore", NULL};
static const char *const k_ram_zones[] = {"dram", "ram", NULL};
/* Package domain: NULL selects the package zone itself. */
static const char *const *const k_zone_names[X86_RAPL_DOMAINS] = {NULL, k_core_zones, k_gpu_zones,
                                                                  k_ram_zones};

/* Socket under discovery: lowest CPU of the package and lowest one in the power PMU cpumask. */
struct socket_probe {
  int id;
  int lead_cpu;
  int mask_cpu;
};

/* Reads a small file into buf (NUL-terminated, trailing whitespace stripped); 0 or -1. */
static int read_text(const char *path, char *buf, size_t cap)
{
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  ssize_t n;

  if (fd < 0)
    return -1;
  n = read(fd, buf, cap - 1);
  close(fd);
  if (n <= 0)
    return -1;
  while (n > 0 && isspace((unsigned char)buf[n - 1]))
    n--;
  buf[n] = '\0';
  return n > 0 ? 0 : -1;
}

/* 1 when cpu is a member of a Linux cpulist such as "0,14" or "0-3,8". */
static int cpulist_has(const char *list, int cpu)
{
  const char *p = list;

  while (*p != '\0') {
    char *end = NULL;
    long lo = strtol(p, &end, 10);
    long hi = lo;

    if (end == p)
      return 0;
    if (*end == '-') {
      p = end + 1;
      hi = strtol(p, &end, 10);
      if (end == p)
        return 0;
    }
    if (cpu >= lo && cpu <= hi)
      return 1;
    p = (*end == ',') ? end + 1 : end;
    if (*end != ',')
      break;
  }
  return 0;
}

static int cpu_package_id(const char *cpu_dir, const char *cpu_name)
{
  char path[512];
  char buf[32];
  char *end = NULL;
  long id;

  if (snprintf(path, sizeof(path), "%s/%s/topology/physical_package_id", cpu_dir, cpu_name) >=
          (int)sizeof(path) ||
      read_text(path, buf, sizeof(buf)) < 0)
    return -1;
  id = strtol(buf, &end, 10);
  return (end != buf && id >= 0 && id < 65536) ? (int)id : -1;
}

/* Index of package id in probes, appending a new entry; -1 on allocation failure. */
static int probe_slot(struct socket_probe **probes, int *n, int id)
{
  struct socket_probe *grown;
  int i;

  for (i = 0; i < *n; i++)
    if ((*probes)[i].id == id)
      return i;
  grown = realloc(*probes, (size_t)(*n + 1) * sizeof(**probes));
  if (grown == NULL)
    return -1;
  *probes = grown;
  grown[*n].id = id;
  grown[*n].lead_cpu = -1;
  grown[*n].mask_cpu = -1;
  return (*n)++;
}

static void note_cpu(struct socket_probe *p, int cpu, const char *pmu_mask)
{
  if (p->lead_cpu < 0 || cpu < p->lead_cpu)
    p->lead_cpu = cpu;
  if (pmu_mask[0] != '\0' && cpulist_has(pmu_mask, cpu) && (p->mask_cpu < 0 || cpu < p->mask_cpu))
    p->mask_cpu = cpu;
}

static int probe_cmp(const void *a, const void *b)
{
  return ((const struct socket_probe *)a)->id - ((const struct socket_probe *)b)->id;
}

/* Every package with an online CPU, sorted by id; count, or -1 on allocation failure. */
static int discover_sockets(const char *sysfs_root, const char *pmu_mask, struct socket_probe **out)
{
  char cpu_dir[512];
  DIR *dir;
  struct dirent *ent;
  int n = 0;

  *out = NULL;
  if (snprintf(cpu_dir, sizeof(cpu_dir), "%s/devices/system/cpu", sysfs_root) >=
          (int)sizeof(cpu_dir) ||
      (dir = opendir(cpu_dir)) == NULL)
    return 0;
  while ((ent = readdir(dir)) != NULL) {
    char *end = NULL;
    long cpu;
    int id, slot;

    if (strncmp(ent->d_name, "cpu", 3) != 0 || !isdigit((unsigned char)ent->d_name[3]))
      continue;
    cpu = strtol(ent->d_name + 3, &end, 10);
    if (*end != '\0' || (id = cpu_package_id(cpu_dir, ent->d_name)) < 0)
      continue;
    slot = probe_slot(out, &n, id);
    if (slot < 0) {
      n = -1;
      break;
    }
    note_cpu(&(*out)[slot], (int)cpu, pmu_mask);
  }
  closedir(dir);
  if (n > 1)
    qsort(*out, (size_t)n, sizeof(**out), probe_cmp);
  return n;
}

/* Joules per count from events/<event>.scale when .unit is Joules; 0 when unusable. */
static double event_joules_per_count(const char *pmu_dir, const char *event)
{
  char path[512];
  char buf[64];
  char *end = NULL;
  double scale;

  if (snprintf(path, sizeof(path), "%s/events/%s.unit", pmu_dir, event) >= (int)sizeof(path) ||
      read_text(path, buf, sizeof(buf)) < 0 || strcmp(buf, "Joules") != 0 ||
      snprintf(path, sizeof(path), "%s/events/%s.scale", pmu_dir, event) >= (int)sizeof(path) ||
      read_text(path, buf, sizeof(buf)) < 0)
    return 0.0;
  scale = strtod(buf, &end);
  return (end != buf && isfinite(scale) && scale > 0.0) ? scale : 0.0;
}

/* Opens one power PMU event; OK, NOT_PRESENT (no event / unusable scale) or the open sentinel. */
static enum stats_sentinel open_perf_domain(struct x86_rapl_counter *c, const char *pmu_dir,
                                            const char *event, int cpu, perf_open_fn open_fn)
{
  struct perf_event_attr attr;
  double jpc = event_joules_per_count(pmu_dir, event);
  double ignored;
  int fd;

  if (jpc <= 0.0 || perf_attr_from_sysfs(pmu_dir, event, &attr) != 0)
    return STATS_SENTINEL_NOT_PRESENT;
  fd = open_fn(&attr, cpu);
  if (fd < 0)
    return perf_open_sentinel(errno);
  c->fd = fd;
  c->mj_per_count = jpc * 1000.0;
  /* Seed so the first collect reports energy since begin; a failed seed re-seeds there. */
  (void)perf_read_scaled(fd, &c->prev, &ignored, NULL, NULL);
  return STATS_SENTINEL_OK;
}

static enum stats_sentinel open_powercap_domain(struct x86_rapl_counter *c, const char *pc_root,
                                                int socket_id, enum x86_rapl_domain d)
{
  char zone[576];
  int rc;

  if (rapl_powercap_find_zone(pc_root, (unsigned)socket_id, k_zone_names[d], zone, sizeof(zone)) <
      0)
    return STATS_SENTINEL_NOT_PRESENT;
  rc = rapl_powercap_counter_open(&c->pc, zone);
  return rc < 0 ? perf_open_sentinel(-rc) : STATS_SENTINEL_OK;
}

/* Perf first, powercap second; when neither opens, the more specific failure is kept. */
static void open_domain(struct x86_rapl_counter *c, const char *pmu_dir, const char *pc_root,
                        const struct x86_rapl_socket *s, enum x86_rapl_domain d,
                        perf_open_fn open_fn)
{
  enum stats_sentinel perf_st = STATS_SENTINEL_NOT_PRESENT;
  enum stats_sentinel pc_st;

  if (pmu_dir != NULL)
    perf_st = open_perf_domain(c, pmu_dir, k_event[d], s->cpu, open_fn);
  if (perf_st == STATS_SENTINEL_OK) {
    c->source = X86_RAPL_SRC_PERF;
    return;
  }
  pc_st = open_powercap_domain(c, pc_root, s->id, d);
  if (pc_st == STATS_SENTINEL_OK) {
    c->source = X86_RAPL_SRC_POWERCAP;
    return;
  }
  c->absent = perf_st != STATS_SENTINEL_NOT_PRESENT ? perf_st : pc_st;
}

static void counter_reset(struct x86_rapl_counter *c)
{
  memset(c, 0, sizeof(*c));
  c->source = X86_RAPL_SRC_NONE;
  c->absent = STATS_SENTINEL_NOT_PRESENT;
  c->fd = -1;
  c->pc.fd = -1;
}

/* One journal line per requested domain: how many sockets read it from each source. */
static void log_domain_sources(const struct x86_rapl *r, unsigned domains)
{
  int d;

  for (d = 0; d < X86_RAPL_DOMAINS; d++) {
    int perf = 0, pc = 0, i;

    if (!(domains & X86_RAPL_BIT(d)))
      continue;
    for (i = 0; i < r->n_sockets; i++) {
      perf += r->sockets[i].dom[d].source == X86_RAPL_SRC_PERF;
      pc += r->sockets[i].dom[d].source == X86_RAPL_SRC_POWERCAP;
    }
    monitor_log_info("x86_rapl: %s on %d of %d sockets via perf power PMU, %d via powercap\n",
                     k_event[d], perf, r->n_sockets, pc);
  }
}

static int open_sockets(struct x86_rapl *r, const struct socket_probe *probes, const char *pmu_dir,
                        const char *pc_root, unsigned domains, perf_open_fn open_fn)
{
  int readable = 0;
  int i, d;

  for (i = 0; i < r->n_sockets; i++) {
    struct x86_rapl_socket *s = &r->sockets[i];
    int any = 0;

    s->id = probes[i].id;
    s->cpu = probes[i].mask_cpu >= 0 ? probes[i].mask_cpu : probes[i].lead_cpu;
    for (d = 0; d < X86_RAPL_DOMAINS; d++) {
      counter_reset(&s->dom[d]);
      if (domains & X86_RAPL_BIT(d))
        open_domain(&s->dom[d], pmu_dir, pc_root, s, (enum x86_rapl_domain)d, open_fn);
      any |= s->dom[d].source != X86_RAPL_SRC_NONE;
    }
    readable += any;
  }
  return readable;
}

int x86_rapl_begin(struct x86_rapl *r, const char *sysfs_root, unsigned domains,
                   perf_open_fn open_fn)
{
  char pmu_dir[512];
  char pc_root[512];
  char mask[1024] = "";
  char path[576];
  struct socket_probe *probes = NULL;
  int have_pmu, n, readable;

  x86_rapl_end(r);
  if (snprintf(pmu_dir, sizeof(pmu_dir), "%s/bus/event_source/devices/power", sysfs_root) >=
          (int)sizeof(pmu_dir) ||
      snprintf(pc_root, sizeof(pc_root), "%s/class/powercap", sysfs_root) >= (int)sizeof(pc_root) ||
      snprintf(path, sizeof(path), "%s/cpumask", pmu_dir) >= (int)sizeof(path)) {
    monitor_log_error("x86_rapl: sysfs root too long: %s\n", sysfs_root);
    return 0;
  }
  have_pmu = read_text(path, mask, sizeof(mask)) == 0;
  n = discover_sockets(sysfs_root, mask, &probes);
  if (n <= 0 || (r->sockets = calloc((size_t)n, sizeof(*r->sockets))) == NULL) {
    monitor_log_error("x86_rapl: no CPU package topology under %s (or out of memory)\n",
                      sysfs_root);
    free(probes);
    return 0;
  }
  r->n_sockets = n;
  readable = open_sockets(r, probes, have_pmu ? pmu_dir : NULL, pc_root, domains, open_fn);
  free(probes);
  log_domain_sources(r, domains);
  return readable;
}

static enum stats_sentinel read_counter(struct x86_rapl_counter *c)
{
  enum stats_sentinel st = STATS_SENTINEL_READ_FAILED;
  double delta = 0.0;
  uint64_t delta_uj = 0;
  int rc;

  switch (c->source) {
  case X86_RAPL_SRC_PERF:
    (void)perf_read_scaled(c->fd, &c->prev, &delta, NULL, &st);
    if (st == STATS_SENTINEL_OK)
      c->total_mj += delta * c->mj_per_count;
    return st;
  case X86_RAPL_SRC_POWERCAP:
    rc = rapl_powercap_counter_read(&c->pc, &delta_uj);
    if (rc == 0)
      c->total_mj += (double)delta_uj / 1000.0;
    return rc == 0 ? STATS_SENTINEL_OK
                   : (rc == -ERANGE ? STATS_SENTINEL_COUNTER_RESET : STATS_SENTINEL_READ_FAILED);
  default:
    return c->absent;
  }
}

void x86_rapl_read(struct x86_rapl *r, int idx, struct x86_rapl_sample out[X86_RAPL_DOMAINS])
{
  int d;

  for (d = 0; d < X86_RAPL_DOMAINS; d++) {
    out[d].mj = 0;
    out[d].status = STATS_SENTINEL_READ_FAILED;
  }
  if (r == NULL || idx < 0 || idx >= r->n_sockets)
    return;
  for (d = 0; d < X86_RAPL_DOMAINS; d++) {
    struct x86_rapl_counter *c = &r->sockets[idx].dom[d];

    out[d].status = read_counter(c);
    if (out[d].status == STATS_SENTINEL_OK)
      out[d].mj = (uint64_t)c->total_mj & X86_RAPL_WIRE_MASK;
  }
}

void x86_rapl_end(struct x86_rapl *r)
{
  int i, d;

  for (i = 0; i < r->n_sockets; i++)
    for (d = 0; d < X86_RAPL_DOMAINS; d++) {
      struct x86_rapl_counter *c = &r->sockets[i].dom[d];

      if (c->fd >= 0)
        close(c->fd);
      rapl_powercap_counter_close(&c->pc);
    }
  free(r->sockets);
  r->sockets = NULL;
  r->n_sockets = 0;
}
