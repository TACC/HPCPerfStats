#include "stats.h"
#include "JOIN.h"
#include "trace.h"
#include "cpuid.h"
#include "intel_uncore_msr_box.h"
#include "intel_topology_walk.h"

#define KEYS                                                                   \
  X(sf_evictions_mes, "E,W=48", ""),                                                 \
      X(llc_lookup_data_read_local, "E,W=48", ""),                                                 \
      X(bypass_cha_imc_all, "E,W=48", ""),                                                 \
      X(llc_lookup_write, "E,W=48", "")

#define CHA_PERF_EVENT(event, umask)                                           \
  ((event) | (umask << 8) | (0x4 << 20))

#define SF_EVICTIONS_MES	      CHA_PERF_EVENT(0x3d, 0x07)
#define LLC_LOOKUP_DATA_READ_LOCAL   CHA_PERF_EVENT(0x34, 0x33)
#define BYPASS_CHA_IMC_ALL	      CHA_PERF_EVENT(0x57, 0x07)
#define LLC_LOOKUP_WRITE	      CHA_PERF_EVENT(0x34, 0x05)

static uint64_t skx_cha_events[] = {
    SF_EVICTIONS_MES,
    LLC_LOOKUP_DATA_READ_LOCAL,
    BYPASS_CHA_IMC_ALL,
    LLC_LOOKUP_WRITE,
};
static const char *const counter_keys[4] = {
    "sf_evictions_mes",
    "llc_lookup_data_read_local",
    "bypass_cha_imc_all",
    "llc_lookup_write",
};

struct skx_cha_begin_ctx {
  int nr;
};

static void skx_cha_begin_visit(void *ctx, char *cpu, int pkg_id, int nr_cores)
{
  struct skx_cha_begin_ctx *c = ctx;
  int j;

  (void)pkg_id;
  for (j = 0; j < nr_cores; j++)
    if (intel_uncore_cha_skx_begin_box(cpu, j, skx_cha_events, 4) == 0)
      c->nr++;
}

static int intel_skx_cha_begin(struct stats_type *type)
{
  struct skx_cha_begin_ctx ctx = {0};

  if (processor != SKYLAKE)
    goto out;

  intel_topology_foreach_pkg_leader_core(&ctx, skx_cha_begin_visit);

out:
  if (ctx.nr == 0)
    type->st_enabled = 0;

  return ctx.nr > 0 ? 0 : -1;
}

struct skx_cha_collect_ctx {
  struct stats_type *type;
};

static void skx_cha_collect_visit(void *ctx, char *cpu, int pkg_id,
				  int nr_cores)
{
  struct skx_cha_collect_ctx *c = ctx;
  int j;

  for (j = 0; j < nr_cores; j++)
    intel_uncore_cha_skx_collect_box(c->type, cpu, pkg_id, j, counter_keys);
}

static void intel_skx_cha_collect(struct stats_type *type)
{
  struct skx_cha_collect_ctx ctx = {type};

  intel_topology_foreach_pkg_leader_core(&ctx, skx_cha_collect_visit);
}

struct stats_type intel_skx_cha_stats_type = {
    .st_name = "intel_x86_uncore_cha_skx",
    .st_begin = &intel_skx_cha_begin,
    .st_collect = &intel_skx_cha_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
