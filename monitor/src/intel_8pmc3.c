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

static void intel_8pmc3_collect_cpu(struct stats_type *type, char *cpu)
{
  struct stats *stats = NULL;
  int msr_fd = -1;
  size_t nkeys = 0;
  const char *const *event_keys = intel_pmc3_event_keys(&nkeys);
  int i;

  stats = get_current_stats(type, cpu);
  if (stats == NULL)
    goto out;

  TRACE("cpu %s\n", cpu);

  msr_fd = msr_open_cpu(cpu, O_RDONLY);
  if (msr_fd < 0)
    goto out;

  for (i = 0; event_keys != NULL && i < (int)nkeys && i < 8; i++) {
    uint64_t val = 0;
    uint32_t msr = IA32_CTR0 + (uint32_t)i;
    if (msr_read_u64(msr_fd, msr, &val) < 0)
      TRACE("cannot read `%s' (%08X) for cpu `%s': %m\n", event_keys[i], msr, cpu);
    else
      stats_set(stats, event_keys[i], val);
  }
  {
    uint64_t val = 0;
    if (msr_read_u64(msr_fd, IA32_FIXED_CTR0, &val) == 0) stats_set(stats, "instr_retired", val);
    if (msr_read_u64(msr_fd, IA32_FIXED_CTR1, &val) == 0) stats_set(stats, "aperf", val);
    if (msr_read_u64(msr_fd, IA32_FIXED_CTR2, &val) == 0) stats_set(stats, "mperf", val);
  }

out:
  if (msr_fd >= 0)
    close(msr_fd);
}

static void intel_8pmc3_collect(struct stats_type *type)
{
  intel_pmc3_core_foreach_cpu(type, intel_8pmc3_collect_cpu);
}

static int intel_8pmc3_begin(struct stats_type *type)
{
  return intel_pmc3_core_begin_if_pmcs(type, 8);
}

struct stats_type intel_8pmc3_stats_type = {
    .st_name = "intel_x86_pmc_gpr8",
    .st_begin = &intel_8pmc3_begin,
    .st_collect = &intel_8pmc3_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
