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
#include "path_open_fail_once.h"
#include "cpuid.h"
#include "amd64_pmc.h"

static  uint64_t amd10h_events[] = {
  FLOPS, 
  MERGE,
  DISPATCH_STALL_CYCLES1,
  DISPATCH_STALL_CYCLES0, 
};

static  uint64_t amd17h_events[] = {
  FLOPS, 
  MERGE,
  BRANCH_INST_RETIRED,
  BRANCH_INST_RETIRED_MISS,
  DISPATCH_STALL_CYCLES1,
  DISPATCH_STALL_CYCLES0, 
};

static  uint64_t amd19h_events[] = {
  FLOPS, 
  MERGE,
  BRANCH_INST_RETIRED,
  BRANCH_INST_RETIRED_MISS,
  DISPATCH_STALL_CYCLES1,
  DISPATCH_STALL_CYCLES0, 
};

static int amd64_pmc_begin_cpu(char *cpu)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t *events;

  // 10H cpus only have 4 counters, 17H and 19H have 6.
  switch(processor) {

  case AMD_10H:
    events = amd10h_events; 
    break;
  case AMD_17H:
    events = amd17h_events;
    break;
  case AMD_19H:
    events = amd19h_events;
    break;
  default:
    ERROR("Processor model/family %d not supported\n", processor);
    goto out;
  }

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

  int i;
  for (i = 0; i < n_pmcs; i++) {
    TRACE("MSR %08X, event %016llX\n", MSR_PERF_CTL0 + i*2, (unsigned long long) events[i]);

    if (pwrite(msr_fd, &events[i], sizeof(events[i]), MSR_PERF_CTL0 + i*2) < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
            (unsigned long long) events[i],
            (unsigned) MSR_PERF_CTL0 + i*2,
            msr_path);
      goto out;
    }
  }

  /* HWCR must be read-modify-write: a write of only (1<<30) clears other bits
   * and is rejected on Zen (EIO / wrmsr "cannot set MSR"). */
  uint64_t hwcr = 0;
  if (pread(msr_fd, &hwcr, sizeof(hwcr), MSR_HW_CONFIG) != (ssize_t) sizeof(hwcr)) {
    ERROR("cannot read HWCR before enabling instr retired ctr (MSR %08X) through `%s': %m\n",
          (unsigned) MSR_HW_CONFIG, msr_path);
    goto out;
  }
  hwcr |= (1ULL << 30);
  if (pwrite(msr_fd, &hwcr, sizeof(hwcr), MSR_HW_CONFIG) < 0) {
    ERROR("cannot enable instr retired ctr at MSR %08X through `%s': %m\n",
          (unsigned) MSR_HW_CONFIG, msr_path);
    goto out;
  }
  /*
  uint64_t zero = 0x00;
  for (i = 0; i < n_pmcs; i++) {
    TRACE("MSR %08X, event %016llX\n", MSR_PERF_CTR0 + i*2, zero);

    if (pwrite(msr_fd, &zero, sizeof(zero), MSR_PERF_CTR0 + i*2) < 0) {
      ERROR("cannot write %016llX to MSR %08X through `%s': %m\n",
            zero,
            (unsigned) MSR_PERF_CTR0 + i*2,
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

static void amd64_pmc_collect_cpu(struct stats_type *type, char *cpu)
{
  char msr_path[80];
  int msr_fd = -1;
  struct stats *stats = NULL;

  stats = get_current_stats(type, cpu);
  if (stats == NULL)
    goto out;

  /* Read MSRs. */
  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  if (path_open_is_skipped(msr_path))
    goto out;
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    path_open_record_failure_once(msr_path);
    goto out;
  }

#define X(k,r...)							\
  ({									\
    uint64_t val = 0;							\
    if (pread(msr_fd, &val, sizeof(val), MSR_PERF_##k) < 0)		\
      TRACE("cannot read `%s' (%08X) through `%s': %m\n", #k, MSR_PERF_##k, msr_path); \
    else								\
      stats_set(stats, #k, val);					\
  })
  KEYS;
#undef X

 out:
  if (msr_fd >= 0)
    close(msr_fd);
}

static void amd64_pmc_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    amd64_pmc_collect_cpu(type, cpu);
  }
}

static int amd64_pmc_begin(struct stats_type *type)
{
  int nr = 0;

  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    if (amd64_pmc_begin_cpu(cpu) == 0)
      nr++;
  }
  
  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

struct stats_type amd64_pmc_stats_type = {
  .st_name = "amd64_pmc",
  .st_begin = &amd64_pmc_begin,
  .st_collect = &amd64_pmc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
