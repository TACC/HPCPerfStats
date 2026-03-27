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
#include "intel_pmc3.h"
#ifdef HAVE_LIKWID
#include "likwid_pmc_adapter.h"
#endif

static int use_likwid_pmc3 = 0;

static uint64_t *intel_pmc3_select_events(void)
{
  uint64_t *events = NULL;
  switch(processor) {
  case NEHALEM:
    events = nhm_events; break;
  case WESTMERE:
    events = nhm_events; break;
  case SANDYBRIDGE:
    events = snb_events; break;
  case IVYBRIDGE:
    events = snb_events; break;
  case HASWELL:
    events = hsw_events; break;
  case BROADWELL:
    events = hsw_events; break;
  case KNL:
    events = knl_events; break;
  case SKYLAKE:
    events = skx_events; break;
  default:
    events = NULL; break;
  }
  return events;
}

//! Configure and start counters for a pmc3 cpu counters
static int intel_pmc3_begin_cpu(char *cpu)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t global_ctr_ctrl, fixed_ctr_ctrl;
  uint64_t *events = intel_pmc3_select_events();
  
  if (events == NULL) {
    ERROR("Processor model/family not supported: %m\n");
    goto out;
  }

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

  /* Disable counters globally. */
  global_ctr_ctrl = 0x0ULL;
  if (pwrite(msr_fd, &global_ctr_ctrl, sizeof(global_ctr_ctrl), IA32_PERF_GLOBAL_CTRL) < 0) {
    ERROR("cannot disable performance counters: %m\n");
    goto out;
  }

  int i;
  for (i = 0; i < n_pmcs; i++) {
    TRACE("MSR %08X, event %016llX\n", IA32_CTL0 + i, (unsigned long long) events[i]);
    if (pwrite(msr_fd, &events[i], sizeof(events[i]), IA32_CTL0 + i) < 0) {
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n",
            (unsigned long long) events[i],
            (unsigned) IA32_CTL0 + i,
            msr_path);
      goto out;
    }
  }
  
  rc = 0;
  /* Enable fixed counters.  Three 4 bit blocks, enable OS, User, Turn off any thread. */
  fixed_ctr_ctrl = 0x333UL;

  if (pwrite(msr_fd, &fixed_ctr_ctrl, sizeof(fixed_ctr_ctrl), IA32_FIXED_CTR_CTRL) < 0)
    ERROR("cannot enable fixed counters: %m\n");

  /* Enable counters globally, n_pmcs PMC and 3 fixed. */
  global_ctr_ctrl = BIT_MASK(n_pmcs) | (0x7ULL << 32);
  if (pwrite(msr_fd, &global_ctr_ctrl, sizeof(global_ctr_ctrl), IA32_PERF_GLOBAL_CTRL) < 0)
    ERROR("cannot enable performance counters: %m\n");

 out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

//! Collect values in counters for cpu
static void intel_pmc3_collect_cpu(struct stats_type *type, char *cpu)
{
  struct stats *stats = NULL;
  char msr_path[80];
  int msr_fd = -1;
  int pmc = 0;

  stats = get_current_stats(type, cpu);
  if (stats == NULL)
    goto out;
#ifdef HAVE_LIKWID
  if (use_likwid_pmc3) {
    uint64_t *events = intel_pmc3_select_events();
    int nr_events = n_pmcs;
    if (events != NULL &&
        likwid_pmc_adapter_read_cpu(stats, atoi(cpu), events, nr_events, n_pmcs) == 0)
      goto out;
  }
#endif

  TRACE("cpu %s\n", cpu);

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

  if (n_pmcs == 8) {
#define X(k,r...)							\
    ({									\
      uint64_t val = 0;							\
      if (pread(msr_fd, &val, sizeof(val), IA32_##k) < 0)		\
	TRACE("cannot read `%s' (%08X) through `%s': %m\n", #k, IA32_##k, msr_path); \
      else								\
	stats_set(stats, #k, val);					\
    })
    KEYS;
#undef X
    goto out;
  }
  
  if (n_pmcs == 4) {
#define X(k,r...)							\
    ({									\
      uint64_t val = 0;							\
      if (pread(msr_fd, &val, sizeof(val), IA32_##k) < 0)		\
	TRACE("cannot read `%s' (%08X) through `%s': %m\n", #k, IA32_##k, msr_path); \
      else								\
	stats_set(stats, #k, val);					\
    })
    HT_KEYS;
#undef X
    goto out;
  }
    
    if (processor == KNL && n_pmcs == 2) {
#define X(k,r...)							\
    ({									\
    uint64_t val = 0;							\
    if (pread(msr_fd, &val, sizeof(val), IA32_##k) < 0)			\
      TRACE("cannot read `%s' (%08X) through `%s': %m\n", #k, IA32_##k, msr_path); \
    else								\
      stats_set(stats, #k, val);					\
  })
    KNL_KEYS;
#undef X
    goto out;
  }

 out:
  if (msr_fd >= 0)
    close(msr_fd);
}

static int intel_pmc3_begin(struct stats_type *type)
{
  int nr = 0;
#ifdef HAVE_LIKWID
  if (likwid_pmc_adapter_init(nr_cpus) == 0) {
    const char *eventset =
      "INSTR_RETIRED_ANY:FIXC0,CPU_CLK_UNHALTED_CORE:FIXC1,CPU_CLK_UNHALTED_REF:FIXC2,"
      "MEM_LOAD_UOPS_RETIRED_L1_HIT:PMC0,MEM_LOAD_UOPS_RETIRED_L2_HIT:PMC1,"
      "MEM_LOAD_UOPS_RETIRED_LLC_HIT:PMC2,L1D_REPLACEMENT:PMC3";
    if (likwid_pmc_adapter_setup_events(eventset) == 0) {
      use_likwid_pmc3 = 1;
      return 0;
    }
    likwid_pmc_adapter_finalize();
  }
#endif
  int i;
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

static void intel_pmc3_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    intel_pmc3_collect_cpu(type, cpu);
  }
}

//! Definition of stats entry for this type
struct stats_type intel_8pmc3_stats_type = {
  .st_name = "intel_pmc3",
  .st_begin = &intel_pmc3_begin,
  .st_collect = &intel_pmc3_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};

//! Definition of stats entry for this type
struct stats_type intel_4pmc3_stats_type = {
  .st_name = "intel_pmc3",
  .st_begin = &intel_pmc3_begin,
  .st_collect = &intel_pmc3_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(HT_KEYS),
#undef X
};

//! Definition of stats entry for this type
struct stats_type intel_knl_stats_type = {
  .st_name = "intel_knl",
  .st_begin = &intel_pmc3_begin,
  .st_collect = &intel_pmc3_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KNL_KEYS),
#undef X
};
