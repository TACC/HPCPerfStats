/* LIKWID overlay on DCGM host_cpu_hw (aarch64). Fail-soft: never disable the type. */
#ifdef MONITOR_CPU_LIKWID_OVERLAY

#include "cpu_counter_metrics_likwid_overlay.h"

#include <stdlib.h>
#include <string.h>

#include "cpu_counter_metrics_likwid_overlay_map.h"
#include "likwid_arch_map.h"
#include "likwid_pmc_adapter.h"
#include "monitor_log.h"
#include "stats.h"
#include "trace.h"

static int g_overlay_ready;
static unsigned long long *g_life_cycles;
static unsigned long long *g_life_instr;
static unsigned long long *g_prev_cycles;
static unsigned long long *g_prev_instr;
static int *g_life_interval_cycles;
static int *g_life_interval_instr;
static int g_life_ncpus;

static void overlay_lifetime_free(void)
{
  free(g_life_cycles);
  free(g_life_instr);
  free(g_prev_cycles);
  free(g_prev_instr);
  free(g_life_interval_cycles);
  free(g_life_interval_instr);
  g_life_cycles = NULL;
  g_life_instr = NULL;
  g_prev_cycles = NULL;
  g_prev_instr = NULL;
  g_life_interval_cycles = NULL;
  g_life_interval_instr = NULL;
  g_life_ncpus = 0;
}

static int overlay_lifetime_alloc(int ncpus)
{
  if (ncpus <= 0)
    return -1;
  overlay_lifetime_free();
  g_life_cycles = (unsigned long long *)calloc((size_t)ncpus, sizeof(*g_life_cycles));
  g_life_instr = (unsigned long long *)calloc((size_t)ncpus, sizeof(*g_life_instr));
  g_prev_cycles = (unsigned long long *)calloc((size_t)ncpus, sizeof(*g_prev_cycles));
  g_prev_instr = (unsigned long long *)calloc((size_t)ncpus, sizeof(*g_prev_instr));
  g_life_interval_cycles = (int *)calloc((size_t)ncpus, sizeof(*g_life_interval_cycles));
  g_life_interval_instr = (int *)calloc((size_t)ncpus, sizeof(*g_life_interval_instr));
  if (g_life_cycles == NULL || g_life_instr == NULL || g_prev_cycles == NULL ||
      g_prev_instr == NULL || g_life_interval_cycles == NULL || g_life_interval_instr == NULL) {
    overlay_lifetime_free();
    return -1;
  }
  g_life_ncpus = ncpus;
  return 0;
}

int cpu_counter_metrics_likwid_overlay_ready(void)
{
  return g_overlay_ready;
}

void cpu_counter_metrics_likwid_overlay_cleanup(void)
{
  if (g_overlay_ready)
    likwid_pmc_adapter_finalize();
  g_overlay_ready = 0;
  overlay_lifetime_free();
}

int cpu_counter_metrics_likwid_overlay_begin(struct stats_type *type)
{
  const char *eventset = likwid_arch_eventset_grace();
  int init_rc;
  int setup_rc;

  (void)type;
  if (g_overlay_ready)
    return 0;

  init_rc = likwid_pmc_adapter_init(nr_cpus);
  if (init_rc != 0) {
    monitor_log_warn("host_cpu_hw: LIKWID overlay init failed (nr_cpus=%d); DCGM util/power only\n",
                     nr_cpus);
    likwid_pmc_adapter_finalize();
    g_overlay_ready = 0;
    return 0;
  }
  setup_rc = likwid_pmc_adapter_setup_events(eventset);
  if (setup_rc != 0) {
    eventset = likwid_arch_eventset_grace_cyc_only();
    setup_rc = likwid_pmc_adapter_setup_events(eventset);
  }
  if (setup_rc != 0) {
    monitor_log_warn("host_cpu_hw: LIKWID overlay eventset failed (events=`%s`); "
                     "DCGM util/power only\n",
                     eventset != NULL ? eventset : "(null)");
    likwid_pmc_adapter_finalize();
    g_overlay_ready = 0;
    return 0;
  }
  if (overlay_lifetime_alloc(nr_cpus) != 0) {
    monitor_log_warn("host_cpu_hw: LIKWID overlay lifetime alloc failed (nr_cpus=%d); "
                     "DCGM util/power only\n",
                     nr_cpus);
    likwid_pmc_adapter_finalize();
    g_overlay_ready = 0;
    return 0;
  }
  g_overlay_ready = 1;
  return 0;
}

int cpu_counter_metrics_likwid_overlay_prepare_collect(void)
{
  if (!g_overlay_ready)
    return -1;
  /* aarch64 prepare_collect is a no-op re-arm; x86 path still re-arms. */
  if (likwid_pmc_adapter_prepare_collect() != 0)
    return -1;
  return likwid_pmc_adapter_read_group();
}

void cpu_counter_metrics_likwid_overlay_collect_cpu(struct stats *stats, int cpu)
{
  struct likwid_overlay_counters c;
  unsigned long long cycles = 0;
  unsigned long long instr = 0;
  unsigned long long life_cycles = 0;
  unsigned long long life_instr = 0;

  if (stats == NULL || !g_overlay_ready)
    return;
  if (cpu < 0 || cpu >= g_life_ncpus || g_life_cycles == NULL)
    return;
  memset(&c, 0, sizeof(c));
  if (likwid_pmc_adapter_read_cpu_cycles_instr(cpu, &cycles, &instr) != 0)
    return;
  life_cycles = likwid_overlay_lifetime_advance(&g_life_cycles[cpu], &g_prev_cycles[cpu],
                                                &g_life_interval_cycles[cpu], cycles);
  life_instr = likwid_overlay_lifetime_advance(&g_life_instr[cpu], &g_prev_instr[cpu],
                                               &g_life_interval_instr[cpu], instr);
  c.have_cycles = 1;
  c.cycles = life_cycles;
  c.have_instr = 1;
  c.instr = life_instr;
  likwid_overlay_map_to_host_cpu_hw(stats, &c);
}

#endif /* MONITOR_CPU_LIKWID_OVERLAY */
