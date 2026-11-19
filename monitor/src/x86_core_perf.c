/* x86_core_perf - Intel / AMD core PMU reader for host_cpu_hw (direct perf, scaled).
 * Portable C: the unit tests drive it on every host through an injected sysfs tree and open
 * function. Raw rows are config = umask << 8 | event on the sysfs `cpu` PMU type; every code was
 * checked with perf against the kernel's named event (FLOP codes also against an exact GEMM
 * FLOP total) on that class before it entered a row. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "monitor_log.h"
#include "stats_sentinel_debug.h"
#include "x86_core_perf.h"

/* Log names, indexed by enum x86_core_event. */
static const char *const g_event_names[X86_CORE_NEVENTS] = {
    "instructions",    "cycles",         "ref_cycles",       "l1d_replacement",  "l1_hit",
    "l2_hit",          "l3_hit",         "fp_scalar_double", "fp_scalar_single", "fp_128b_double",
    "fp_128b_single",  "fp_256b_double", "fp_256b_single",   "fp_512b_double",   "fp_512b_single",
    "amd_ret_instr",   "amd_ret_brn",    "amd_ret_brn_misp", "amd_ls_dispatch",  "amd_flops",
    "amd_flops_dp_pd", "amd_flops_dp_sd"};

/* Architectural events X86_CORE_INSTRUCTIONS .. X86_CORE_REF_CYCLES: the kernel's sysfs alias
 * first, else the generic PERF_TYPE_HARDWARE id (same scaled read format, not pinned). */
#define X86_CORE_NGENERIC (X86_CORE_REF_CYCLES + 1)
static const struct {
  const char *sysfs;
  uint64_t hw;
} g_generic[X86_CORE_NGENERIC] = {
    {"instructions", PERF_COUNT_HW_INSTRUCTIONS},
    {"cpu-cycles", PERF_COUNT_HW_CPU_CYCLES},
    {"ref-cycles", PERF_COUNT_HW_REF_CPU_CYCLES},
};

/* Raw codes and D6 counter budget of one CPU class. A class without a row reads only the
 * architectural events and has no solo baseline; a new class needs codes verified on it. */
struct x86_core_row {
  unsigned int code[X86_CORE_NEVENTS]; /* umask << 8 | event; 0 = the class has no such event */
  unsigned int gp_counters;            /* general-purpose counters per CPU */
  unsigned int pair_event;     /* event select the kernel schedules on an adjacent pair of general
                              counters; 0 = none */
  unsigned char generic_fixed; /* instructions / cycles / ref-cycles run on fixed counters */
  unsigned char ref_cycles;    /* the class has ref-cycles */
};

/* Skylake-SP through Granite Rapids: 8 general counters per CPU with HT off (every measured
 * node) plus fixed counters for instructions, cycles and ref-cycles. */
static const struct x86_core_row g_intel_server = {
    .code =
        {
            [X86_CORE_L1D_REPLACEMENT] = 0x0151,
            [X86_CORE_L1_HIT] = 0x01d1,
            [X86_CORE_L2_HIT] = 0x02d1,
            [X86_CORE_L3_HIT] = 0x04d1,
            [X86_CORE_FP_SCALAR_DOUBLE] = 0x01c7,
            [X86_CORE_FP_SCALAR_SINGLE] = 0x02c7,
            [X86_CORE_FP_128B_DOUBLE] = 0x04c7,
            [X86_CORE_FP_128B_SINGLE] = 0x08c7,
            [X86_CORE_FP_256B_DOUBLE] = 0x10c7,
            [X86_CORE_FP_256B_SINGLE] = 0x20c7,
            [X86_CORE_FP_512B_DOUBLE] = 0x40c7,
            [X86_CORE_FP_512B_SINGLE] = 0x80c7,
        },
    .gp_counters = 8,
    .generic_fixed = 1,
    .ref_cycles = 1,
};

/* Zen3 / Zen4: 6 general counters, no fixed ones; event 0x03 (retired SSE/AVX FLOPs, umask
 * 0xff = every op type) needs a merged counter pair. */
static const struct x86_core_row g_amd_zen3_zen4 = {
    .code =
        {
            [X86_CORE_AMD_RET_INSTR] = 0x00c0,
            [X86_CORE_AMD_RET_BRN] = 0x00c2,
            [X86_CORE_AMD_RET_BRN_MISP] = 0x00c3,
            [X86_CORE_AMD_LS_DISPATCH] = 0x0729,
            [X86_CORE_AMD_FLOPS] = 0xff03,
        },
    .gp_counters = 6,
    .pair_event = 0x03,
};

/* Zen5: event 0x03 op-type bits 3:0 (0x0f = all) combine with format bits 7:5 (0xa0 packed
 * double, 0x80 scalar double); single precision is the total minus double. */
static const struct x86_core_row g_amd_zen5 = {
    .code =
        {
            [X86_CORE_AMD_RET_INSTR] = 0x00c0,
            [X86_CORE_AMD_RET_BRN] = 0x00c2,
            [X86_CORE_AMD_RET_BRN_MISP] = 0x00c3,
            [X86_CORE_AMD_LS_DISPATCH] = 0x0729,
            [X86_CORE_AMD_FLOPS] = 0x0f03,
            [X86_CORE_AMD_FLOPS_DP_PD] = 0xaf03,
            [X86_CORE_AMD_FLOPS_DP_SD] = 0x8f03,
        },
    .gp_counters = 6,
    .pair_event = 0x03,
};

static const char *const g_class_names[] = {
    [AMD_10H] = "amd_10h",
    [AMD_ROME] = "amd_rome",
    [AMD_MILAN] = "amd_milan",
    [AMD_GENOA] = "amd_genoa",
    [AMD_TURIN] = "amd_turin",
    [NEHALEM] = "nehalem",
    [WESTMERE] = "westmere",
    [SANDYBRIDGE] = "sandybridge",
    [IVYBRIDGE] = "ivybridge",
    [HASWELL] = "haswell",
    [BROADWELL] = "broadwell",
    [SKYLAKE] = "skylake",
    [SKYLAKE_X] = "skylake_x",
    [CASCADE_LAKE] = "cascade_lake",
    [ICELAKE_SERVER] = "icelake_server",
    [SAPPHIRE_RAPIDS] = "sapphire_rapids",
    [EMERALD_RAPIDS] = "emerald_rapids",
    [GRANITE_RAPIDS] = "granite_rapids",
    [SIERRA_FOREST] = "sierra_forest",
    [ARM_GRACE] = "arm_grace",
};

struct x86_core_slot {
  int fd;
  unsigned char open_code; /* enum stats_sentinel why fd < 0 */
  struct perf_prev prev;
  unsigned long long life;
};

/* Per-CPU D6 time lifetimes (ns) and the solo share of each open general counter slot. */
struct x86_core_cpu_times {
  unsigned long long enabled;
  unsigned long long running;
  double solo;
  double gp_share; /* min(1, gp_counters / open general slots); 0 without a row */
};

static struct x86_core_slot *g_slots;      /* ncpus x X86_CORE_NEVENTS */
static struct x86_core_cpu_times *g_times; /* ncpus */
/* General counters event e occupies: 0 on a fixed counter, 2 for the paired event, else 1. */
static unsigned char g_event_gp_slots[X86_CORE_NEVENTS];
/* 1 + the lower-indexed event with the same attr (AMD sysfs `instructions` is raw 0xc0) whose
 * counter event e shares instead of opening a second one; 0 = own fds. */
static unsigned char g_alias_of[X86_CORE_NEVENTS];
static int g_have_solo;
static int g_ncpus;
static int g_ready;

const char *x86_core_perf_event_name(int ev)
{
  return (ev >= 0 && ev < X86_CORE_NEVENTS) ? g_event_names[ev] : NULL;
}

static const char *class_name(processor_t proc)
{
  unsigned int i = (unsigned int)proc;

  return (i < sizeof(g_class_names) / sizeof(g_class_names[0]) && g_class_names[i] != NULL)
             ? g_class_names[i]
             : "unknown";
}

static const struct x86_core_row *class_row(processor_t proc)
{
  switch (proc) {
  case SKYLAKE_X:
  case CASCADE_LAKE:
  case ICELAKE_SERVER:
  case SAPPHIRE_RAPIDS:
  case EMERALD_RAPIDS:
  case GRANITE_RAPIDS:
    return &g_intel_server;
  case AMD_MILAN:
  case AMD_GENOA:
    return &g_amd_zen3_zen4;
  case AMD_TURIN:
    return &g_amd_zen5;
  default:
    return NULL;
  }
}

/* ref-cycles: as the row says; without a row, every non-AMD class (Genoa has no ref-cycles
 * and no AMD key reads it). */
static int class_has_ref_cycles(processor_t proc, const struct x86_core_row *row)
{
  if (row != NULL)
    return row->ref_cycles;
  switch (proc) {
  case AMD_10H:
  case AMD_ROME:
  case AMD_MILAN:
  case AMD_GENOA:
  case AMD_TURIN:
    return 0;
  default:
    return 1;
  }
}

static void cfg_defaults(const struct x86_core_perf_cfg *in, struct x86_core_perf_cfg *out)
{
  memset(out, 0, sizeof(*out));
  if (in != NULL)
    *out = *in;
  if (out->devices_dir == NULL)
    out->devices_dir = PERF_DEVICES_DIR;
  if (out->open_fn == NULL)
    out->open_fn = perf_open_cpu;
}

int x86_core_perf_resolve(processor_t proc, const struct x86_core_perf_cfg *cfg,
                          struct perf_event_attr attrs[X86_CORE_NEVENTS], int ok[X86_CORE_NEVENTS])
{
  struct x86_core_perf_cfg c;
  const struct x86_core_row *row = class_row(proc);
  uint32_t type = 0;
  char pmu_dir[512];
  int e, n = 0;

  if (attrs == NULL || ok == NULL)
    return -1;
  cfg_defaults(cfg, &c);
  memset(ok, 0, sizeof(ok[0]) * X86_CORE_NEVENTS);
  if (snprintf(pmu_dir, sizeof(pmu_dir), "%s/cpu", c.devices_dir) >= (int)sizeof(pmu_dir) ||
      perf_pmu_type(pmu_dir, &type) != 0)
    return -1;
  for (e = 0; e < X86_CORE_NEVENTS; e++) {
    if (e < X86_CORE_NGENERIC) {
      if (e == X86_CORE_REF_CYCLES && !class_has_ref_cycles(proc, row))
        continue;
      if (perf_attr_from_sysfs(pmu_dir, g_generic[e].sysfs, &attrs[e]) != 0)
        perf_attr_raw(&attrs[e], PERF_TYPE_HARDWARE, g_generic[e].hw);
      ok[e] = 1;
    } else if (row != NULL && row->code[e] != 0) {
      perf_attr_raw(&attrs[e], type, row->code[e]);
      ok[e] = 1;
    }
    n += ok[e];
  }
  return n;
}

void x86_core_perf_cleanup(void)
{
  int i;

  for (i = 0; g_slots != NULL && i < g_ncpus * X86_CORE_NEVENTS; i++) {
    if (g_slots[i].fd >= 0)
      close(g_slots[i].fd);
  }
  free(g_slots);
  free(g_times);
  g_slots = NULL;
  g_times = NULL;
  g_have_solo = 0;
  memset(g_event_gp_slots, 0, sizeof(g_event_gp_slots));
  memset(g_alias_of, 0, sizeof(g_alias_of));
  g_ncpus = 0;
  g_ready = 0;
}

/* Opens event @p e on every CPU; one log line when any CPU fails. @return CPUs opened. */
static int open_event_all_cpus(const struct x86_core_perf_cfg *c, int e,
                               const struct perf_event_attr *attr)
{
  int cpu, opened = 0, first_errno = 0;

  for (cpu = 0; cpu < g_ncpus; cpu++) {
    struct x86_core_slot *s = &g_slots[cpu * X86_CORE_NEVENTS + e];

    errno = 0;
    s->fd = STATS_FAULT("perf_open", g_event_names[e], &errno) ? -1 : c->open_fn(attr, cpu);
    if (s->fd >= 0) {
      opened++;
      continue;
    }
    if (first_errno == 0)
      first_errno = errno != 0 ? errno : EINVAL;
    s->open_code = (unsigned char)perf_open_sentinel(errno != 0 ? errno : EINVAL);
  }
  if (opened < g_ncpus)
    monitor_log_warn("x86_core_perf: %s open failed on %d of %d CPU(s) (%s); key emits its "
                     "sentinel there\n",
                     g_event_names[e], g_ncpus - opened, g_ncpus, strerror(first_errno));
  return opened;
}

/* 1 + the lower-indexed resolved event with the same type and config as @p e, or 0. */
static int same_attr_as(const struct perf_event_attr attrs[X86_CORE_NEVENTS],
                        const int ok[X86_CORE_NEVENTS], int e)
{
  int j;

  for (j = 0; j < e; j++) {
    if (ok[j] && attrs[j].type == attrs[e].type && attrs[j].config == attrs[e].config)
      return j + 1;
  }
  return 0;
}

/* General counters event @p e costs (D6): none on a fixed counter, two for the paired event. */
static unsigned char event_gp_slots(const struct x86_core_row *row, int e)
{
  if (row == NULL)
    return 1;
  if (e < X86_CORE_NGENERIC)
    return row->generic_fixed ? 0 : 1;
  return (row->pair_event != 0 && (row->code[e] & 0xffU) == row->pair_event) ? 2 : 1;
}

/* D6 solo baseline per CPU: the share each general slot gets with only the monitor on the PMU
 * (the kernel rotates the open general events over gp_counters). */
static void init_solo_shares(const struct x86_core_row *row)
{
  unsigned int gp = row != NULL ? row->gp_counters : 0;
  int cpu, e;

  g_have_solo = gp > 0;
  for (cpu = 0; cpu < g_ncpus; cpu++) {
    unsigned int n_slots = 0;

    for (e = 0; e < X86_CORE_NEVENTS; e++) {
      if (g_slots[cpu * X86_CORE_NEVENTS + e].fd >= 0)
        n_slots += g_event_gp_slots[e];
    }
    g_times[cpu].gp_share =
        (!g_have_solo || n_slots == 0) ? 0.0 : (n_slots <= gp ? 1.0 : (double)gp / (double)n_slots);
  }
}

int x86_core_perf_begin(int ncpus, processor_t proc, const struct x86_core_perf_cfg *cfg)
{
  struct x86_core_perf_cfg c;
  struct perf_event_attr attrs[X86_CORE_NEVENTS];
  int ok[X86_CORE_NEVENTS];
  const struct x86_core_row *row = class_row(proc);
  int e, i, n_events = 0;

  x86_core_perf_cleanup();
  cfg_defaults(cfg, &c);
  if (ncpus <= 0 || x86_core_perf_resolve(proc, &c, attrs, ok) < 0) {
    monitor_log_warn("x86_core_perf: no cpu PMU under %s; host_cpu_hw keeps /proc/stat util "
                     "only\n",
                     c.devices_dir);
    return -1;
  }
  if (row == NULL)
    monitor_log_info("x86_core_perf: no verified raw-event row for class %s; only the "
                     "architectural events are read, other core keys emit NOT_PRESENT\n",
                     class_name(proc));
  g_slots = (struct x86_core_slot *)calloc((size_t)ncpus * X86_CORE_NEVENTS, sizeof(*g_slots));
  g_times = (struct x86_core_cpu_times *)calloc((size_t)ncpus, sizeof(*g_times));
  if (g_slots == NULL || g_times == NULL) {
    x86_core_perf_cleanup();
    return -1;
  }
  g_ncpus = ncpus;
  for (i = 0; i < ncpus * X86_CORE_NEVENTS; i++) {
    g_slots[i].fd = -1;
    g_slots[i].open_code = STATS_SENTINEL_NOT_PRESENT; /* no row and no sysfs name */
  }
  for (e = 0; e < X86_CORE_NEVENTS; e++) {
    if (!ok[e] || (g_alias_of[e] = (unsigned char)same_attr_as(attrs, ok, e)) != 0)
      continue;
    g_event_gp_slots[e] = event_gp_slots(row, e);
    if (open_event_all_cpus(&c, e, &attrs[e]) > 0)
      n_events++;
  }
  if (n_events == 0) {
    x86_core_perf_cleanup();
    return -1;
  }
  init_solo_shares(row);
  monitor_log_info("x86_core_perf: %d core counters open on %d CPU(s), class %s (scaled by "
                   "enabled/running; %u general counters%s)\n",
                   n_events, ncpus, class_name(proc), row != NULL ? row->gp_counters : 0,
                   g_have_solo ? "" : ", unknown: no solo baseline");
  g_ready = 1;
  return 0;
}

int x86_core_perf_ready(void)
{
  return g_ready;
}

void x86_core_perf_collect_cpu(int cpu, struct x86_core_counts *out)
{
  struct x86_core_cpu_times *t;
  int e;

  if (out == NULL)
    return;
  memset(out, 0, sizeof(*out));
  memset(out->code, STATS_SENTINEL_NOT_PRESENT, sizeof(out->code));
  if (!g_ready || cpu < 0 || cpu >= g_ncpus)
    return;
  t = &g_times[cpu];
  for (e = 0; e < X86_CORE_NEVENTS; e++) {
    struct x86_core_slot *s = &g_slots[cpu * X86_CORE_NEVENTS + e];
    struct perf_times dt;
    double delta = 0.0;
    enum stats_sentinel st;

    if (g_alias_of[e] != 0) { /* the source event was read earlier in this loop */
      int src = g_alias_of[e] - 1;

      out->life[e] = out->life[src];
      out->have[e] = out->have[src];
      out->code[e] = out->code[src];
      continue;
    }
    if (s->fd < 0) {
      out->code[e] = s->open_code;
      continue;
    }
    /* life only grows: a failed read keeps prev, so the next delta spans the gap. */
    if (perf_read_scaled(s->fd, &s->prev, &delta, &dt, &st) == 0 && delta > 0.0)
      s->life += (unsigned long long)(delta + 0.5);
    out->code[e] = (unsigned char)st;
    t->enabled += dt.enabled;
    t->running += dt.running;
    t->solo += (double)dt.enabled * (g_event_gp_slots[e] == 0 ? 1.0 : t->gp_share);
    out->life[e] = s->life;
    out->have[e] = 1;
    out->have_times = 1;
  }
  out->pmu_enabled_ns = t->enabled;
  out->pmu_running_ns = t->running;
  out->have_solo = (unsigned char)(out->have_times && g_have_solo);
  out->pmu_solo_ns = out->have_solo ? (unsigned long long)(t->solo + 0.5) : 0ULL;
}
