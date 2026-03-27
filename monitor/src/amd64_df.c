#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include <fcntl.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "amd64_df.h"

static  uint64_t amd17h_df_events[] = {
  EVENT_DRAM_CHANNEL_0,
  EVENT_DRAM_CHANNEL_1,
  EVENT_DRAM_CHANNEL_2,
  EVENT_DRAM_CHANNEL_3,
};

static  uint64_t amd19h_df_events[] = {
  EVENT_DRAM_CHANNEL_0,
  EVENT_DRAM_CHANNEL_1,
  EVENT_DRAM_CHANNEL_2,
  EVENT_DRAM_CHANNEL_3,
};

static int amd64_df_begin_cpu(char *cpu)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t *events;
  uint64_t *df_events;

  // 17H and 19H have 4 MC (DataFabric) Counters we can access.
  switch(processor) {

  case AMD_17H:
    df_events = amd17h_df_events; 
    break;
  case AMD_19H:
    df_events = amd19h_df_events; 
    break;
  default:
    TRACE("Processor model/family %d not supported\n", processor);
    goto out;
  }

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

  /* Memory Counters */
  int i;
  for (i = 0; i < 4; i++) {
    TRACE("MSR %08X, event %016llX\n", MSR_DF_CTL0 + i*2, (unsigned long long) df_events[i]);

    if (pwrite(msr_fd, &df_events[i], sizeof(df_events[i]), MSR_DF_CTL0 + i*2) < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
            (unsigned long long) df_events[i],
            (unsigned) MSR_DF_CTL0 + i*2,
            msr_path);
      goto out;
    }
  }
  /*
  uint64_t zero = 0x00; 
  for (i = 0; i < 4; i++) {
    TRACE("MSR %08X, event %016llX\n", MSR_DF_CTR0 + i*2, (unsigned long long) 0);

    if (pwrite(msr_fd, &zero, sizeof(zero), MSR_DF_CTR0 + i*2) < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
            (unsigned long long) df_events[i],
            (unsigned) MSR_DF_CTR0 + i*2,
            msr_path);
      goto out;
    }
  }
  */

  rc = 0;

 out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

static void amd64_df_collect_cpu(struct stats_type *type, char *cpu)
{
  char msr_path[80];
  int msr_fd = -1;
  struct stats *stats = NULL;

  stats = get_current_stats(type, cpu);
  if (stats == NULL)
    goto out;

  /* Read MSRs. */
  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

#define X(k,r...)							\
  ({									\
    uint64_t val = 0;							\
    if (pread(msr_fd, &val, sizeof(val), MSR_DF_##k) < 0)		\
      TRACE("cannot read `%s' (%08X) through `%s': %m\n", #k, MSR_DF_##k, msr_path); \
    else								\
      stats_set(stats, #k, val);					\
  })
  KEYS;
#undef X

 out:
  if (msr_fd >= 0)
    close(msr_fd);
}

static void amd64_df_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    int pkg, core, smt, nr_core;

    if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) && (core == 0) && (smt == 0)) {
      amd64_df_collect_cpu(type, cpu);
    }
  }
}

static int amd64_df_begin(struct stats_type *type)
{
  int nr = 0;

  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    int pkg, core, smt, nr_core;

    if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) && (core == 0) && (smt == 0))
      if (amd64_df_begin_cpu(cpu) == 0)
	nr++;
  }
  
  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

struct stats_type amd64_df_stats_type = {
  .st_name = "amd64_df",
  .st_begin = &amd64_df_begin,
  .st_collect = &amd64_df_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
