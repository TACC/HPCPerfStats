#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>

#include "intel_pmc3.h"
#include "intel_pmc3_core.h"
#include "stats.h"
#include "trace.h"
#include "msr_io.h"

static void intel_knl_collect_cpu(struct stats_type *type, char *cpu)
{
  struct stats *stats = NULL;
  int msr_fd = -1;

  stats = get_current_stats(type, cpu);
  if (stats == NULL)
    goto out;

  TRACE("cpu %s\n", cpu);

  msr_fd = msr_open_cpu(cpu, O_RDONLY);
  if (msr_fd < 0)
    goto out;

#define X(k, r...)                                                             \
  ({                                                                         \
    uint64_t val = 0;                                                        \
    if (msr_read_u64(msr_fd, IA32_##k, &val) < 0)                            \
      TRACE("cannot read `%s' (%08X) for cpu `%s': %m\n", #k, IA32_##k,      \
	    cpu);                                                            \
    else                                                                     \
      stats_set(stats, #k, val);                                             \
  })
    KNL_KEYS;
#undef X

out:
  if (msr_fd >= 0)
    close(msr_fd);
}

static void intel_knl_collect(struct stats_type *type)
{
  intel_pmc3_core_foreach_cpu(type, intel_knl_collect_cpu);
}

static int intel_knl_begin(struct stats_type *type)
{
  return intel_pmc3_core_begin_if_pmcs(type, 2);
}

struct stats_type intel_knl_stats_type = {
    .st_name = "intel_knl",
    .st_begin = &intel_knl_begin,
    .st_collect = &intel_knl_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KNL_KEYS),
#undef X
};
