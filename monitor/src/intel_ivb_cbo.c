#include "stats.h"
#include "JOIN.h"
#include "trace.h"
#include "cpuid.h"
#include "intel_uncore_msr_box.h"
#include "intel_topology_walk.h"

#define KEYS                                                                 \
  X(LLC_LOOKUP_DATA_READ, "E,W=44", ""),                                    \
      X(LLC_LOOKUP_WRITE, "E,W=44", ""),                                    \
      X(RING_IV_USED, "E,W=44", ""),                                        \
      X(COUNTER0_OCCUPANCY, "E,W=44", "")

#define CBOX_PERF_EVENT(event, umask)                                        \
  ((event) | (umask << 8) | (0ULL << 17) | (0ULL << 18) | (0ULL << 19)        \
   | (1ULL << 22) | (0ULL << 23) | (0x01ULL << 24))

#define LLC_LOOKUP_DATA_READ CBOX_PERF_EVENT(0x34, 0x03)
#define LLC_LOOKUP_WRITE	CBOX_PERF_EVENT(0x34, 0x05)
#define RING_IV_USED		CBOX_PERF_EVENT(0x1E, 0x0F)
#define COUNTER0_OCCUPANCY	CBOX_PERF_EVENT(0x1F, 0x00)

static uint64_t events[] = {
    LLC_LOOKUP_DATA_READ,
    LLC_LOOKUP_WRITE,
    RING_IV_USED,
    COUNTER0_OCCUPANCY,
};
static const char *const counter_keys[4] = {
    "LLC_LOOKUP_DATA_READ",
    "LLC_LOOKUP_WRITE",
    "RING_IV_USED",
    "COUNTER0_OCCUPANCY",
};

struct ivb_cbo_begin_ctx {
  int nr;
};

static void ivb_cbo_begin_visit(void *ctx, char *cpu, int pkg_id, int nr_cores)
{
  struct ivb_cbo_begin_ctx *c = ctx;
  int j;

  (void)pkg_id;
  for (j = 0; j < nr_cores; j++)
    if (intel_uncore_cbo_snb_ivb_begin_box(cpu, j, events, 4) == 0)
      c->nr++;
}

static int intel_ivb_cbo_begin(struct stats_type *type)
{
  struct ivb_cbo_begin_ctx ctx = {0};

  if (processor != IVYBRIDGE)
    goto out;

  intel_topology_foreach_pkg_leader_core(&ctx, ivb_cbo_begin_visit);

out:
  if (ctx.nr == 0)
    type->st_enabled = 0;

  return ctx.nr > 0 ? 0 : -1;
}

struct ivb_cbo_collect_ctx {
  struct stats_type *type;
};

static void ivb_cbo_collect_visit(void *ctx, char *cpu, int pkg_id,
				  int nr_cores)
{
  struct ivb_cbo_collect_ctx *c = ctx;
  int j;

  for (j = 0; j < nr_cores; j++)
    intel_uncore_cbo_snb_ivb_collect_box(c->type, cpu, pkg_id, j, counter_keys);
}

static void intel_ivb_cbo_collect(struct stats_type *type)
{
  struct ivb_cbo_collect_ctx ctx = {type};

  intel_topology_foreach_pkg_leader_core(&ctx, ivb_cbo_collect_visit);
}

struct stats_type intel_ivb_cbo_stats_type = {
    .st_name = "intel_ivb_cbo",
    .st_begin = &intel_ivb_cbo_begin,
    .st_collect = &intel_ivb_cbo_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
