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
#include "pscanf.h"
#include "cpuid.h"

/*! \name CBo Performance Monitoring Global Control Registers */

#define CBOX_CTL0 0xD04
#define CBOX_CTL1 0xD24
#define CBOX_CTL2 0xD44
#define CBOX_CTL3 0xD64
#define CBOX_CTL4 0xD84
#define CBOX_CTL5 0xDA4
#define CBOX_CTL6 0xDC4
#define CBOX_CTL7 0xDE4

#define CBOX_FILTER0 0xD14
#define CBOX_FILTER1 0xD34
#define CBOX_FILTER2 0xD54
#define CBOX_FILTER3 0xD74
#define CBOX_FILTER4 0xD94
#define CBOX_FILTER5 0xDB4
#define CBOX_FILTER6 0xDD4
#define CBOX_FILTER7 0xDF4

#define CTL0 0xD10
#define CTL1 0xD11
#define CTL2 0xD12
#define CTL3 0xD13

#define CTR0 0xD16
#define CTR1 0xD17
#define CTR2 0xD18
#define CTR3 0xD19

#define KEYS \
    X(CTL0, "C", ""), \
    X(CTL1, "C", ""), \
    X(CTL2, "C", ""), \
    X(CTL3, "C", ""),  \
    X(CTR0, "E,W=44", ""), \
    X(CTR1, "E,W=44", ""), \
    X(CTR2, "E,W=44", ""), \
    X(CTR3, "E,W=44", "")

/*! \brief Filter   
  Can filter by opcode, MESIF state, node, core, thread
 */
#define CBOX_FILTER(...)  \
  ( (0x0ULL << 0)  \
  | (0x00ULL << 10) \
  | (0x1FULL << 18)  \
  | (0x000ULL << 23) \
  )

/*! \brief Event select
  
  Events are listed in Table 2-14.  They are defined in detail
  in Section 2.3.7.
  
  To change events to count:
  -# Define event below
  -# Modify events array in intel_ivb_cbo_begin()
*/
#define CBOX_PERF_EVENT(event, umask) \
  ( (event) \
  | (umask << 8) \
  | (0ULL << 17) \
  | (0ULL << 18) \
  | (0ULL << 19) \
  | (1ULL << 22) \
  | (0ULL << 23) \
  | (0x01ULL << 24) \
  )

/*! \name Events */
#define CLOCK_TICKS         CBOX_PERF_EVENT(0x00, 0x00) //!< CTR0-3
#define RxR_OCCUPANCY       CBOX_PERF_EVENT(0x11, 0x01) //!< CTR0
#define COUNTER0_OCCUPANCY  CBOX_PERF_EVENT(0x1F, 0x00) //!< CTR1-3
#define LLC_LOOKUP_DATA_READ CBOX_PERF_EVENT(0x34, 0x03) //!< CTR0-3
#define LLC_LOOKUP_WRITE     CBOX_PERF_EVENT(0x34, 0x05) //!< CTR0-3
#define LLC_LOOKUP_VICTIMS   CBOX_PERF_EVENT(0x37, 0x0F)
#define RING_IV_USED         CBOX_PERF_EVENT(0x1E, 0x0F)
//@}

//! Configure and start counters for CBo
static int intel_ivb_cbo_begin_box(char *cpu, int box, uint64_t *events, size_t nr_events)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t ctl;
  uint64_t filter;
  int offset = box*32;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

  ctl = 0x10100ULL; // enable freeze (bit 16), freeze (bit 8)
  /* CBo ctrl registers are 32-bits apart */
  if (pwrite(msr_fd, &ctl, sizeof(ctl), CBOX_CTL0 + offset) < 0) {
    ERROR("cannot enable freeze of CBo counter: %m\n");
    goto out;
  }

  /* Filtering by opcode, MESIF state, node, core and thread possible */
  filter = CBOX_FILTER();
  if (pwrite(msr_fd, &filter, sizeof(filter), CBOX_FILTER0 + offset) < 0) {
    ERROR("cannot modify CBo filters: %m\n");
    goto out;
  }

  /* Select Events for this C-Box */
  int i;
  for (i = 0; i < nr_events; i++) {
    TRACE("MSR %08X, event %016llX\n", CTL0 + offset + i, (unsigned long long) events[i]);
    if (pwrite(msr_fd, &events[i], sizeof(events[i]), CTL0 + offset + i) < 0) { 
      ERROR("cannot write event %016llX to MSR %08X through `%s': %m\n", 
            (unsigned long long) events[i],
            (unsigned) CTL0 + offset + i,
            msr_path);
      goto out;
    }
  }
  
  /* Unfreeze CBo counter (64-bit) */
  ctl = 0x10000ULL; // unfreeze counter
  if (pwrite(msr_fd, &ctl, sizeof(ctl), CBOX_CTL0 + offset) < 0) {
    ERROR("cannot unfreeze CBo counters: %m\n");
    goto out;
  }

  rc = 0;

 out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

static uint64_t events[] = {
  LLC_LOOKUP_DATA_READ, 
  LLC_LOOKUP_WRITE,
  RING_IV_USED,
  COUNTER0_OCCUPANCY
};

//! Configure and start counters
static int intel_ivb_cbo_begin(struct stats_type *type)
{
  int n_pmcs = 0;
  int nr = 0;

  int i,j;
  if (processor != IVYBRIDGE) goto out;
  
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    int pkg_id = -1;
    int core_id = -1;
    int smt_id = -1;
    int nr_cores;
    snprintf(cpu, sizeof(cpu), "%d", i);
    cpuid_read_cpu_topology(cpu, &pkg_id, &core_id, &smt_id, &nr_cores);
    if (smt_id == 0 && core_id == 0)
      for (j = 0; j < nr_cores; j++)
	if (intel_ivb_cbo_begin_box(cpu, j, events, 4) == 0)
	  nr++;
  }
  
 out:
  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

//! Collect values in counters for a CBo
static void intel_ivb_cbo_collect_box(struct stats_type *type, char *cpu, int pkg_id, int box)
{
  struct stats *stats = NULL;
  char msr_path[80];
  int msr_fd = -1;
  int offset;
  offset = 32*box;

  char pkg_box[80];
  snprintf(pkg_box, sizeof(pkg_box), "%d/%d", pkg_id, box);
  TRACE("cpu %s\n", cpu);
  TRACE("pkg_id/box %s\n", pkg_box);
  stats = get_current_stats(type, pkg_box);
  if (stats == NULL)
    goto out;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDONLY);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

#define X(k,r...) \
  ({ \
    uint64_t val = 0; \
    if (pread(msr_fd, &val, sizeof(val), k + offset) < 0) \
      ERROR("cannot read `%s' (%08X) through `%s': %m\n", #k, k + offset, msr_path); \
    else \
      stats_set(stats, #k, val); \
  })
  KEYS;
#undef X

 out:
  if (msr_fd >= 0)
    close(msr_fd);
}

//! Collect values in counters
static void intel_ivb_cbo_collect(struct stats_type *type)
{
  int i,j;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    int pkg_id = -1;
    int core_id = -1;
    int smt_id = -1;
    int nr_cores = 0;
    snprintf(cpu, sizeof(cpu), "%d", i);
    cpuid_read_cpu_topology(cpu, &pkg_id, &core_id, &smt_id, &nr_cores);
    if (smt_id == 0 && core_id == 0)
      for (j = 0; j < nr_cores; j++)
        intel_ivb_cbo_collect_box(type, cpu, pkg_id, j);
  }
}

//! Definition of stats for this type
struct stats_type intel_ivb_cbo_stats_type = {
  .st_name = "intel_ivb_cbo",
  .st_begin = &intel_ivb_cbo_begin,
  .st_collect = &intel_ivb_cbo_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
