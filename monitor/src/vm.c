/* host_vm — virtual memory counters from /proc/vmstat. */
#include <stddef.h>
#include "stats.h"
#include "trace.h"
#include "collect.h"

#define KEYS                                                                                       \
  X(nr_anon_transparent_hugepages, "", ""), X(pgpgin, "E,U=KB", ""), X(pgpgout, "E,U=KB", ""),     \
      X(pswpin, "E", ""), X(pswpout, "E", ""), X(pgalloc_normal, "E", ""), X(pgfree, "E", ""),     \
      X(pgactivate, "E", ""), X(pgdeactivate, "E", ""), X(pgfault, "E", ""),                       \
      X(pgmajfault, "E", ""), X(pgrefill_normal, "E", ""), X(pgsteal_normal, "E", ""),             \
      X(pgscan_kswapd_normal, "E", ""), X(pgscan_direct_normal, "E", ""),                          \
      X(pginodesteal, "E", ""), X(slabs_scanned, "E", ""), X(kswapd_steal, "E", ""),               \
      X(kswapd_inodesteal, "E", ""), X(pageoutrun, "E", ""), X(allocstall, "E", ""),               \
      X(pgrotated, "E", ""), X(thp_fault_alloc, "E", ""), X(thp_fault_fallback, "E", ""),          \
      X(thp_collapse_alloc, "E", ""), X(thp_collapse_alloc_failed, "E", ""), X(thp_split, "E", "")

static void vm_collect(struct stats_type *type)
{
  struct stats *stats = NULL;

  stats = get_current_stats(type, NULL);
  if (stats == NULL)
    return;

  path_collect_key_value("/proc/vmstat", stats);
}

struct stats_type vm_stats_type = {
    .st_name = "host_vm",
    .st_collect = &vm_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
