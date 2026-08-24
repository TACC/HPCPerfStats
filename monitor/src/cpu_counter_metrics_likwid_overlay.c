/* LIKWID overlay on DCGM host_cpu_hw (aarch64). Fail-soft: never disable the type. */
#ifdef MONITOR_CPU_LIKWID_OVERLAY

#include "cpu_counter_metrics_likwid_overlay.h"

#include <string.h>

#include "cpu_counter_metrics_likwid_overlay_map.h"
#include "likwid_arch_map.h"
#include "likwid_pmc_adapter.h"
#include "monitor_log.h"
#include "stats.h"
#include "trace.h"

static int g_overlay_ready;

int cpu_counter_metrics_likwid_overlay_ready(void)
{
  return g_overlay_ready;
}

void cpu_counter_metrics_likwid_overlay_cleanup(void)
{
  if (g_overlay_ready)
    likwid_pmc_adapter_finalize();
  g_overlay_ready = 0;
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
  g_overlay_ready = 1;
  return 0;
}

int cpu_counter_metrics_likwid_overlay_prepare_collect(void)
{
  if (!g_overlay_ready)
    return -1;
  if (likwid_pmc_adapter_prepare_collect() != 0)
    return -1;
  return likwid_pmc_adapter_read_group();
}

void cpu_counter_metrics_likwid_overlay_collect_cpu(struct stats *stats, int cpu)
{
  struct likwid_overlay_counters c;
  unsigned long long cycles = 0;
  unsigned long long instr = 0;

  if (stats == NULL || !g_overlay_ready)
    return;
  memset(&c, 0, sizeof(c));
  if (likwid_pmc_adapter_read_cpu_cycles_instr(cpu, &cycles, &instr) != 0)
    return;
  c.have_cycles = 1;
  c.cycles = cycles;
  c.have_instr = 1;
  c.instr = instr;
  likwid_overlay_map_to_host_cpu_hw(stats, &c);
}

#endif /* MONITOR_CPU_LIKWID_OVERLAY */
