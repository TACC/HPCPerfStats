#include "stats.h"
#include "JOIN.h"
#include "trace.h"
#include "cpuid.h"
#include "intel_uncore_msr_box.h"
#include "intel_topology_walk.h"

#define KEYS                                                                 \
  X(RxR_OCCUPANCY, "E,W=48", ""),                                                 \
      X(LLC_LOOKUP_DATA_READ, "E,W=48", ""),                                                 \
      X(RING_IV_USED, "E,W=48", ""),                                                 \
      X(LLC_LOOKUP_WRITE, "E,W=48", "")

#define CBOX_PERF_EVENT(event, umask)                                        \
  ((event) | (umask << 8) | (0ULL << 17) | (0ULL << 18) | (0ULL << 19)        \
   | (1ULL << 22) | (0ULL << 23) | (0x01ULL << 24))

#define RxR_OCCUPANCY		 CBOX_PERF_EVENT(0x11, 0x01)
#define LLC_LOOKUP_DATA_READ	 CBOX_PERF_EVENT(0x34, 0x03)
#define RING_IV_USED		 CBOX_PERF_EVENT(0x1E, 0x0F)
#define LLC_LOOKUP_WRITE	 CBOX_PERF_EVENT(0x34, 0x05)

static uint64_t events[] = {
    RxR_OCCUPANCY,
    LLC_LOOKUP_DATA_READ,
    RING_IV_USED,
    LLC_LOOKUP_WRITE,
};
static const char *const counter_keys[4] = {
    "RxR_OCCUPANCY",
    "LLC_LOOKUP_DATA_READ",
    "RING_IV_USED",
    "LLC_LOOKUP_WRITE",
};

struct bdw_cbo_begin_ctx {
  int nr;
};

static void bdw_cbo_begin_visit(void *ctx, char *cpu, int pkg_id, int nr_cores)
{
  struct bdw_cbo_begin_ctx *c = ctx;
  int j;

  (void)pkg_id;
  for (j = 0; j < nr_cores; j++)
    if (intel_uncore_cbo_hsw_bdw_begin_box(cpu, j, events, 4) == 0)
      c->nr++;
}

static int intel_bdw_cbo_begin(struct stats_type *type)
{
  struct bdw_cbo_begin_ctx ctx = {0};

  if (processor != BROADWELL)
    goto out;

  intel_topology_foreach_pkg_leader_core(&ctx, bdw_cbo_begin_visit);

out:
  if (ctx.nr == 0)
    type->st_enabled = 0;

  return ctx.nr > 0 ? 0 : -1;
}

struct bdw_cbo_collect_ctx {
  struct stats_type *type;
};

static void bdw_cbo_collect_visit(void *ctx, char *cpu, int pkg_id,
				  int nr_cores)
{
  struct bdw_cbo_collect_ctx *c = ctx;
  int j;

  for (j = 0; j < nr_cores; j++)
    intel_uncore_cbo_hsw_bdw_collect_box(c->type, cpu, pkg_id, j, counter_keys);
}

static void intel_bdw_cbo_collect(struct stats_type *type)
{
  struct bdw_cbo_collect_ctx ctx = {type};

  intel_topology_foreach_pkg_leader_core(&ctx, bdw_cbo_collect_visit);
}

struct stats_type intel_bdw_cbo_stats_type = {
    .st_name = "intel_bdw_cbo",
    .st_begin = &intel_bdw_cbo_begin,
    .st_collect = &intel_bdw_cbo_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
