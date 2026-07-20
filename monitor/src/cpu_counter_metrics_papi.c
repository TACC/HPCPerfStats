/* PAPI FLOPs/cycles overlay for DCGM host_cpu_hw on aarch64 (Grace). */
#ifdef MONITOR_CPU_PAPI_FLOPS

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <papi.h>

#include "cpu_counter_metrics_papi.h"
#include "cpu_counter_metrics_papi_map.h"
#include "monitor_log.h"
#include "stats.h"
#include "trace.h"

extern int nr_cpus;

enum {
  PAPI_SLOT_CYC = 0,
  PAPI_SLOT_SP = 1,
  PAPI_SLOT_DP = 2,
  PAPI_SLOT_INS = 3,
  PAPI_SLOT_INT8 = 4,
  PAPI_SLOT_INT16 = 5,
  PAPI_SLOT_N = 6
};

static int g_papi_ready;
static int g_papi_warned;
static int *g_eventset; /* per CPU; PAPI_NULL if unused */
/* Ordered list of event codes actually added to each eventset. */
static int g_active_codes[PAPI_SLOT_N];
static int g_active_slots[PAPI_SLOT_N];
static int g_n_active;

static void papi_warn_once(const char *msg)
{
  if (g_papi_warned)
    return;
  g_papi_warned = 1;
  monitor_log_warn("cpu_counter_metrics_papi: %s\n", msg);
}

static int papi_resolve_event(int preset, const char *const *native_names, int *out_code)
{
  int code = PAPI_NULL;
  int rc;
  int i;

  if (out_code == NULL)
    return -1;

  if (preset != PAPI_NULL) {
    rc = PAPI_query_event(preset);
    if (rc == PAPI_OK) {
      *out_code = preset;
      return 0;
    }
  }

  if (native_names == NULL)
    return -1;
  for (i = 0; native_names[i] != NULL; i++) {
    rc = PAPI_event_name_to_code((char *)native_names[i], &code);
    if (rc != PAPI_OK)
      continue;
    rc = PAPI_query_event(code);
    if (rc == PAPI_OK) {
      *out_code = code;
      return 0;
    }
  }
  return -1;
}

static int papi_probe_active_events(void)
{
  static const char *sp_names[] = { "FP_SCALE_OPS_SPEC", "VFP_SPEC", NULL };
  static const char *dp_names[] = { "ASE_SVE_FP64_SPEC", NULL };
  static const char *cyc_names[] = { "CPU_CYCLES", NULL };
  static const char *ins_names[] = { "INST_RETIRED", NULL };
  static const char *int8_names[] = { "ASE_SVE_INT8_SPEC", NULL };
  static const char *int16_names[] = { "ASE_SVE_INT16_SPEC", NULL };
  int code;

  g_n_active = 0;

  if (papi_resolve_event(PAPI_TOT_CYC, cyc_names, &code) == 0) {
    g_active_codes[g_n_active] = code;
    g_active_slots[g_n_active] = PAPI_SLOT_CYC;
    g_n_active++;
  }
  if (papi_resolve_event(PAPI_SP_OPS, sp_names, &code) == 0) {
    g_active_codes[g_n_active] = code;
    g_active_slots[g_n_active] = PAPI_SLOT_SP;
    g_n_active++;
  }
  if (papi_resolve_event(PAPI_DP_OPS, dp_names, &code) == 0) {
    g_active_codes[g_n_active] = code;
    g_active_slots[g_n_active] = PAPI_SLOT_DP;
    g_n_active++;
  }
  if (papi_resolve_event(PAPI_TOT_INS, ins_names, &code) == 0) {
    g_active_codes[g_n_active] = code;
    g_active_slots[g_n_active] = PAPI_SLOT_INS;
    g_n_active++;
  }
  if (papi_resolve_event(PAPI_NULL, int8_names, &code) == 0) {
    g_active_codes[g_n_active] = code;
    g_active_slots[g_n_active] = PAPI_SLOT_INT8;
    g_n_active++;
  }
  if (papi_resolve_event(PAPI_NULL, int16_names, &code) == 0) {
    g_active_codes[g_n_active] = code;
    g_active_slots[g_n_active] = PAPI_SLOT_INT16;
    g_n_active++;
  }

  return g_n_active;
}

static int papi_setup_cpu_eventset(int cpu)
{
  int es = PAPI_NULL;
  int rc;
  int i;
  PAPI_option_t opt;

  rc = PAPI_create_eventset(&es);
  if (rc != PAPI_OK)
    return -1;

  rc = PAPI_assign_eventset_component(es, 0);
  if (rc != PAPI_OK)
    goto fail;

  memset(&opt, 0, sizeof(opt));
  opt.cpu.eventset = es;
  opt.cpu.cpu_num = cpu;
  rc = PAPI_set_opt(PAPI_CPU_ATTACH, &opt);
  if (rc != PAPI_OK)
    goto fail;

  (void)PAPI_set_multiplex(es);

  for (i = 0; i < g_n_active; i++) {
    rc = PAPI_add_event(es, g_active_codes[i]);
    if (rc != PAPI_OK)
      goto fail;
  }

  rc = PAPI_start(es);
  if (rc != PAPI_OK)
    goto fail;

  g_eventset[cpu] = es;
  return 0;

fail:
  if (es != PAPI_NULL) {
    PAPI_cleanup_eventset(es);
    PAPI_destroy_eventset(&es);
  }
  return -1;
}

void cpu_counter_metrics_papi_cleanup(void)
{
  int i;

  if (g_eventset != NULL) {
    for (i = 0; i < nr_cpus; i++) {
      if (g_eventset[i] != PAPI_NULL) {
	(void)PAPI_stop(g_eventset[i], NULL);
	PAPI_cleanup_eventset(g_eventset[i]);
	PAPI_destroy_eventset(&g_eventset[i]);
	g_eventset[i] = PAPI_NULL;
      }
    }
    free(g_eventset);
    g_eventset = NULL;
  }
  g_n_active = 0;
  g_papi_ready = 0;
}

int cpu_counter_metrics_papi_ready(void)
{
  return g_papi_ready;
}

int cpu_counter_metrics_papi_begin(struct stats_type *type)
{
  int i;
  int rc;
  int ok_cpus = 0;

  (void)type;
  cpu_counter_metrics_papi_cleanup();

  if (nr_cpus <= 0) {
    papi_warn_once("nr_cpus <= 0; PAPI FLOPs/cycles disabled");
    return 0;
  }

  rc = PAPI_library_init(PAPI_VER_CURRENT);
  if (rc != PAPI_VER_CURRENT && rc <= 0) {
    papi_warn_once("PAPI_library_init failed; PAPI FLOPs/cycles disabled");
    return 0;
  }

  (void)PAPI_multiplex_init();
  (void)PAPI_set_domain(PAPI_DOM_ALL);

  if (papi_probe_active_events() <= 0) {
    papi_warn_once("no usable PAPI events; FLOPs/cycles disabled");
    return 0;
  }

  g_eventset = calloc((size_t)nr_cpus, sizeof(*g_eventset));
  if (g_eventset == NULL) {
    papi_warn_once("calloc eventset failed");
    return -1;
  }
  for (i = 0; i < nr_cpus; i++)
    g_eventset[i] = PAPI_NULL;

  for (i = 0; i < nr_cpus; i++) {
    if (papi_setup_cpu_eventset(i) == 0)
      ok_cpus++;
  }

  if (ok_cpus <= 0) {
    papi_warn_once("PAPI CPU attach failed for all CPUs");
    cpu_counter_metrics_papi_cleanup();
    return 0;
  }

  g_papi_ready = 1;
  TRACE("papi begin: ok_cpus=%d n_active=%d\n", ok_cpus, g_n_active);
  return 0;
}

void cpu_counter_metrics_papi_collect_cpu(struct stats *stats, int cpu)
{
  struct papi_cpu_hw_counters c;
  long long vals[PAPI_SLOT_N];
  int rc;
  int i;

  if (!g_papi_ready || stats == NULL || cpu < 0 || cpu >= nr_cpus)
    return;
  if (g_eventset == NULL || g_eventset[cpu] == PAPI_NULL)
    return;

  memset(vals, 0, sizeof(vals));
  rc = PAPI_read(g_eventset[cpu], vals);
  if (rc != PAPI_OK)
    return;

  memset(&c, 0, sizeof(c));
  for (i = 0; i < g_n_active; i++) {
    unsigned long long v = (unsigned long long)vals[i];

    switch (g_active_slots[i]) {
    case PAPI_SLOT_CYC:
      c.have_cycles = 1;
      c.cycles = v;
      break;
    case PAPI_SLOT_SP:
      c.have_sp = 1;
      c.sp_ops = v;
      break;
    case PAPI_SLOT_DP:
      c.have_dp = 1;
      c.dp_ops = v;
      break;
    case PAPI_SLOT_INS:
      c.have_instr = 1;
      c.instr = v;
      break;
    case PAPI_SLOT_INT8:
      c.have_int8 = 1;
      c.int8_ops = v;
      break;
    case PAPI_SLOT_INT16:
      c.have_int16 = 1;
      c.int16_ops = v;
      break;
    default:
      break;
    }
  }

  papi_map_counters_to_host_cpu_hw(stats, &c);
}

#endif /* MONITOR_CPU_PAPI_FLOPS */
