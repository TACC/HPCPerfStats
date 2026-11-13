/* arm_core_perf — Grace / aarch64 core PMU reader for host_cpu_hw (direct perf, scaled).
 * Portable C (no #ifdef aarch64): the daemon links it only on the `arm` backend, while the
 * unit tests drive it on every host through an injected sysfs tree and open function. */
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "arm_core_perf.h"
#include "monitor_log.h"

#define ARM_CORE_MIDR_PATH "/sys/devices/system/cpu/cpu0/regs/identification/midr_el1"
#define ARM_CORE_CPUINFO_PATH "/proc/cpuinfo"
#define ARM_CORE_PMU_PREFIX "armv8_pmuv3"
#define ARM_CORE_MIDR_IMPLEMENTER(m) (((m) >> 24) & 0xffULL)
#define ARM_CORE_MIDR_PART(m) (((m) >> 4) & 0xfffULL)

/* sysfs names, indexed by enum arm_core_event (kernel armv8 PMU event aliases). */
static const char *const g_event_names[ARM_CORE_NEVENTS] = {
    "cpu_cycles",        "inst_retired",      "fp_dp_spec",
    "fp_sp_spec",        "fp_hp_spec",        "fp_fixed_ops_spec",
    "fp_scale_ops_spec", "ase_sve_int8_spec", "ase_sve_int16_spec"};

/* Raw codes used when the kernel does not name an event (finding #8: Grace names only
 * cpu_cycles / inst_retired). 0 = no code. A new core needs a row verified on that hardware. */
struct arm_core_midr_row {
  unsigned int implementer;
  unsigned int part;
  unsigned int code[ARM_CORE_NEVENTS];
};

static const struct arm_core_midr_row g_midr_rows[] = {
    /* Arm Neoverse V2 (NVIDIA Grace, MIDR 0x410fd4f0); LIKWID nvidia_grace + perf r<code>. */
    {0x41, 0xd4f, {0, 0, 0x801C, 0x8018, 0x8014, 0x80C1, 0x80C0, 0x80E3, 0x80E7}},
};

struct arm_core_slot {
  int fd;
  struct arm_perf_prev prev;
  unsigned long long life;
};

static struct arm_core_slot *g_slots; /* ncpus x ARM_CORE_NEVENTS */
static int g_ncpus;
static int g_ready;

const char *arm_core_perf_event_name(int ev)
{
  return (ev >= 0 && ev < ARM_CORE_NEVENTS) ? g_event_names[ev] : NULL;
}

/* "CPU implementer\t: 0x41" -> 0x41; -1 when @p line is not @p key. */
static long cpuinfo_field(const char *line, const char *key)
{
  const char *colon;
  char *end = NULL;
  long v;

  if (strncmp(line, key, strlen(key)) != 0 || (colon = strchr(line, ':')) == NULL)
    return -1;
  v = strtol(colon + 1, &end, 0);
  return (end == colon + 1) ? -1 : v;
}

static int midr_from_cpuinfo(const char *path, unsigned long long *midr)
{
  char line[256];
  long impl = -1, part = -1;
  FILE *f = fopen(path, "re");

  if (f == NULL)
    return -1;
  while ((impl < 0 || part < 0) && fgets(line, sizeof(line), f) != NULL) {
    if (impl < 0)
      impl = cpuinfo_field(line, "CPU implementer");
    if (part < 0)
      part = cpuinfo_field(line, "CPU part");
  }
  fclose(f);
  if (impl < 0 || part < 0)
    return -1;
  *midr = ((unsigned long long)impl << 24) | ((unsigned long long)part << 4);
  return 0;
}

int arm_core_perf_read_midr(const char *midr_path, const char *cpuinfo_path,
                            unsigned long long *midr)
{
  char buf[64];
  char *end = NULL;
  FILE *f;

  if (midr == NULL)
    return -1;
  f = (midr_path != NULL) ? fopen(midr_path, "re") : NULL;
  if (f != NULL) {
    int ok = fgets(buf, sizeof(buf), f) != NULL;

    fclose(f);
    if (ok) {
      *midr = strtoull(buf, &end, 0);
      if (end != buf)
        return 0;
    }
  }
  return (cpuinfo_path != NULL) ? midr_from_cpuinfo(cpuinfo_path, midr) : -1;
}

static const struct arm_core_midr_row *midr_row(unsigned long long midr)
{
  size_t i;

  for (i = 0; i < sizeof(g_midr_rows) / sizeof(g_midr_rows[0]); i++) {
    if (ARM_CORE_MIDR_IMPLEMENTER(midr) == g_midr_rows[i].implementer &&
        ARM_CORE_MIDR_PART(midr) == g_midr_rows[i].part)
      return &g_midr_rows[i];
  }
  return NULL;
}

/* Lowest-named armv8_pmuv3* PMU under @p devices_dir (armv8_pmuv3_0 on Grace). */
static int find_core_pmu(const char *devices_dir, char *out, size_t cap)
{
  DIR *d = opendir(devices_dir);
  struct dirent *de;
  int found = 0;

  if (d == NULL)
    return -1;
  while ((de = readdir(d)) != NULL) {
    if (strncmp(de->d_name, ARM_CORE_PMU_PREFIX, strlen(ARM_CORE_PMU_PREFIX)) != 0)
      continue;
    if (found && strcmp(de->d_name, strrchr(out, '/') + 1) >= 0)
      continue;
    if (snprintf(out, cap, "%s/%s", devices_dir, de->d_name) < (int)cap)
      found = 1;
  }
  closedir(d);
  return found ? 0 : -1;
}

static void cfg_defaults(const struct arm_core_perf_cfg *in, struct arm_core_perf_cfg *out)
{
  memset(out, 0, sizeof(*out));
  if (in != NULL)
    *out = *in;
  if (out->devices_dir == NULL)
    out->devices_dir = ARM_PERF_DEVICES_DIR;
  if (out->midr_path == NULL)
    out->midr_path = ARM_CORE_MIDR_PATH;
  if (out->cpuinfo_path == NULL)
    out->cpuinfo_path = ARM_CORE_CPUINFO_PATH;
  if (out->open_fn == NULL)
    out->open_fn = arm_perf_open_cpu;
}

int arm_core_perf_resolve(const struct arm_core_perf_cfg *cfg,
                          struct perf_event_attr attrs[ARM_CORE_NEVENTS], int ok[ARM_CORE_NEVENTS],
                          unsigned long long *midr_out)
{
  struct arm_core_perf_cfg c;
  const struct arm_core_midr_row *row;
  uint32_t type = 0;
  int have_type;
  unsigned long long midr = 0;
  char pmu_dir[512];
  int e, n = 0;

  if (attrs == NULL || ok == NULL)
    return -1;
  cfg_defaults(cfg, &c);
  memset(ok, 0, sizeof(ok[0]) * ARM_CORE_NEVENTS);
  if (midr_out != NULL)
    *midr_out = 0;
  if (find_core_pmu(c.devices_dir, pmu_dir, sizeof(pmu_dir)) != 0)
    return -1;
  have_type = (arm_perf_pmu_type(pmu_dir, &type) == 0); /* raw codes go on this PMU type */
  if (arm_core_perf_read_midr(c.midr_path, c.cpuinfo_path, &midr) != 0)
    midr = 0;
  if (midr_out != NULL)
    *midr_out = midr;
  row = midr_row(midr);
  for (e = 0; e < ARM_CORE_NEVENTS; e++) {
    if (arm_perf_attr_from_sysfs(pmu_dir, g_event_names[e], &attrs[e]) == 0)
      ok[e] = 1;
    else if (row != NULL && row->code[e] != 0 && have_type) {
      arm_perf_attr_raw(&attrs[e], type, row->code[e]);
      ok[e] = 1;
    }
    n += ok[e];
  }
  return n;
}

void arm_core_perf_cleanup(void)
{
  int i;

  for (i = 0; g_slots != NULL && i < g_ncpus * ARM_CORE_NEVENTS; i++) {
    if (g_slots[i].fd >= 0)
      close(g_slots[i].fd);
  }
  free(g_slots);
  g_slots = NULL;
  g_ncpus = 0;
  g_ready = 0;
}

/* Logs which events are unavailable on this core (once per begin, not per CPU). */
static void log_unresolved(const int ok[ARM_CORE_NEVENTS], unsigned long long midr)
{
  int e;

  for (e = 0; e < ARM_CORE_NEVENTS; e++) {
    if (!ok[e])
      monitor_log_info("arm_core_perf: %s not named in sysfs and no raw-code row for MIDR "
                       "0x%llx; key stays 0\n",
                       g_event_names[e], midr);
  }
}

/* Opens event @p e on every CPU; one log line when any CPU fails. @return CPUs opened. */
static int open_event_all_cpus(const struct arm_core_perf_cfg *c, int e,
                               const struct perf_event_attr *attr)
{
  int cpu, opened = 0, first_errno = 0;

  for (cpu = 0; cpu < g_ncpus; cpu++) {
    struct arm_core_slot *s = &g_slots[cpu * ARM_CORE_NEVENTS + e];

    errno = 0;
    s->fd = c->open_fn(attr, cpu);
    if (s->fd >= 0)
      opened++;
    else if (first_errno == 0)
      first_errno = errno != 0 ? errno : EINVAL;
  }
  if (opened < g_ncpus)
    monitor_log_warn("arm_core_perf: %s open failed on %d of %d CPU(s) (%s); key stays 0 there\n",
                     g_event_names[e], g_ncpus - opened, g_ncpus, strerror(first_errno));
  return opened;
}

int arm_core_perf_begin(int ncpus, const struct arm_core_perf_cfg *cfg)
{
  struct arm_core_perf_cfg c;
  struct perf_event_attr attrs[ARM_CORE_NEVENTS];
  int ok[ARM_CORE_NEVENTS];
  unsigned long long midr = 0;
  int e, i, n_events = 0;

  arm_core_perf_cleanup();
  cfg_defaults(cfg, &c);
  if (ncpus <= 0 || arm_core_perf_resolve(&c, attrs, ok, &midr) < 0) {
    monitor_log_warn("arm_core_perf: no " ARM_CORE_PMU_PREFIX " PMU under %s; "
                     "host_cpu_hw keeps /proc/stat util only\n",
                     c.devices_dir);
    return -1;
  }
  log_unresolved(ok, midr);
  g_slots = (struct arm_core_slot *)calloc((size_t)ncpus * ARM_CORE_NEVENTS, sizeof(*g_slots));
  if (g_slots == NULL)
    return -1;
  g_ncpus = ncpus;
  for (i = 0; i < ncpus * ARM_CORE_NEVENTS; i++)
    g_slots[i].fd = -1;
  for (e = 0; e < ARM_CORE_NEVENTS; e++) {
    if (ok[e] && open_event_all_cpus(&c, e, &attrs[e]) > 0)
      n_events++;
  }
  if (n_events == 0) {
    arm_core_perf_cleanup();
    return -1;
  }
  monitor_log_info("arm_core_perf: %d of %d core events open on %d CPU(s), MIDR 0x%llx "
                   "(scaled by enabled/running)\n",
                   n_events, ARM_CORE_NEVENTS, ncpus, midr);
  g_ready = 1;
  return 0;
}

int arm_core_perf_ready(void)
{
  return g_ready;
}

void arm_core_perf_collect_cpu(int cpu, struct arm_core_counts *out)
{
  int e;

  if (out == NULL)
    return;
  memset(out, 0, sizeof(*out));
  if (!g_ready || cpu < 0 || cpu >= g_ncpus)
    return;
  for (e = 0; e < ARM_CORE_NEVENTS; e++) {
    struct arm_core_slot *s = &g_slots[cpu * ARM_CORE_NEVENTS + e];
    double delta = 0.0;

    if (s->fd < 0)
      continue;
    if (arm_perf_read_scaled(s->fd, &s->prev, &delta) == 0 && delta > 0.0)
      s->life += (unsigned long long)(delta + 0.5);
    out->life[e] = s->life;
    out->have[e] = 1;
  }
}
