#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include "stats.h"
#include "trace.h"
#include "msr_io.h"
#include "intel_pmc3.h"

//! Collect values in counters for cpu
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

#define X(k,r...)							\
  ({									\
    uint64_t val = 0;							\
    if (msr_read_u64(msr_fd, IA32_##k, &val) < 0)			\
      TRACE("cannot read `%s' (%08X) for cpu `%s': %m\n", #k, IA32_##k, cpu); \
    else								\
      stats_set(stats, #k, val);					\
  })
  KNL_KEYS;
#undef X

 out:
  if (msr_fd >= 0)
    close(msr_fd);
}

static void intel_knl_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    intel_knl_collect_cpu(type, cpu);
  }
}
static int intel_knl_begin(struct stats_type *type)
{
  int nr = 0;
  int i;
  if (n_pmcs == 2) 
    for (i = 0; i < nr_cpus; i++) {
      char cpu[80];
      snprintf(cpu, sizeof(cpu), "%d", i);    
      if (intel_pmc3_begin_cpu(cpu) == 0)
	nr++;
    }  
  if (nr == 0) 
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

//! Definition of stats entry for this type
struct stats_type intel_knl_stats_type = {
  .st_name = "intel_knl",
  .st_begin = &intel_knl_begin,
  .st_collect = &intel_knl_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KNL_KEYS),
#undef X
};
