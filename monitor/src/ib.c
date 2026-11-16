/* host_ib - InfiniBand HCA port counters (sysfs + optional MAD). */
#include "stats.h"
#include "ib.h"
#include "ib_family.h"

struct stats_type ib_stats_type = {
    .st_name = "host_ib",
    .st_collect = &ib_family_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
