/* host_ib — InfiniBand HCA port counters (sysfs + optional MAD). */
#include "stats.h"
#include "ib.h"
#include "ib_family.h"

static void ib_collect(struct stats_type *type)
{
  ib_family_collect(type);
}

struct stats_type ib_stats_type = {
    .st_name = "host_ib",
    .st_collect = &ib_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
