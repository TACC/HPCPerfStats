/*! 
 \file intel_hsw_cbo.c
 \author Todd Evans 
 \brief Performance Monitoring Counters for Intel Haswell Caching Agents (CBos)


  \par Details such as Tables and Figures can be found in:
  "Intel® Xeon® Processor E5-2600 Product Family Uncore 
  Performance Monitoring Guide" 
  Reference Number: 331051 September 2014 \n
  CBo monitoring is described in Section 2.3.

  \note
  Haswell microarchitectures have signatures 06_3c, 06_45, 06_46, 06_47, and EP is 06_3f


  \par Location of cpu info and monitoring register files:

  ex) Display cpuid and msr file for cpu 0:

      $ ls -l /dev/cpu/0
      total 0
      crw-------  1 root root 203, 0 Oct 28 18:47 cpuid
      crw-------  1 root root 202, 0 Oct 28 18:47 msr


   \par MSR address layout of registers:

   Layout shown in Table 2-8.
   There are up to 18 CBos per socket.
   There are 4 configurable counter registers per CBo.  These routines
   only collect data from core_id 0 msr on each socket.
   
   There are 4 configure, 4 counter, 1  CBo global control, 
   and 1 CBo filter registers per CBo
*/

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

/*! \name CBo Performance Monitoring Global Control Registers

   Configure register layout shown in Table 2-14.  
   These registers control every
   counter within a CBo.  They can freeze
   individual CBos control and counter registers.

   \note
   Documentation says they are 32 bit but only 64 bit
   works.

   @{
*/

#define CBOX_CTL0 0xE00

//@}

/*! \name CBo filter registers

  Layout in Table 2-12. Can filter CBo counters' recorded events by 
  Opcode, MESIF state, core, and/or Hyperthread.
  
  ~~~
  opcode             [31:23]
  state              [22:18]
  node               [17:10]
  core               [3:1] 
  thread             [0] 
  ~~~  

  Opcodes are listed in Table 2-13 and defined in
  Table 2-144.

  \note
  Documentation says they are 32 bit but only 64 bit
  works.
@{
 */

#define CBOX_FILTER0_0 0xE05
#define CBOX_FILTER1_0 0xE06

//@}

/*! \name CBo Performance Monitoring Registers
  
  Control register layout in 2-16.  
  These are used to select events.  There are
  We specify base address and increment by 
  1 intra-CBo and 16 inter-CBo.
  ~~~
  threshhold        [31:24]
  invert threshold  [23]
  enable            [22]
  tid filter enable [19]
  edge detect       [18]
  clear counter     [17]
  umask             [15:8]
  event select      [7:0]
  ~~~ 

  \note
  Documentation says they are 32 bit but only 64 bit
  works.

  Counter register layout in 2-17.  These are 64 bit but counters
  are only 48 bits wide.

  @{
 */
#define CTL0 0xE01
#define CTL1 0xE02
#define CTL2 0xE03
#define CTL3 0xE04

#define CTR0 0xE08
#define CTR1 0xE09
#define CTR2 0xE0A
#define CTR3 0xE0B
//@}

/*! \brief KEYS will define the raw schema for this type. 
  
  The required order of registers is:
  -# Control registers in order
  -# Counter registers in order
*/
#define KEYS \
    X(CTL0, "C", ""), \
    X(CTL1, "C", ""), \
    X(CTL2, "C", ""), \
    X(CTL3, "C", ""),  \
    X(CTR0, "E,W=48", ""), \
    X(CTR1, "E,W=48", ""), \
    X(CTR2, "E,W=48", ""), \
    X(CTR3, "E,W=48", "")

/*! \brief Filters
  Table 2-18
  Can filter by opcode, MESIF state, node, core, thread
 */
#define CBOX_FILTER0(...)  \
  ( (0x0ULL << 0)  \
  | (0x00ULL << 10) \
  | (0x3FULL << 18)  \
  | (0x000ULL << 23) \
  )
/*! \brief Filters
  Table 2-19
  Can filter opcdoes
 */
#define CBOX_FILTER1(...)  \
  ( (0x0000ULL << 0)  \
  | (0x00ULL << 20) \
  | (0x0ULL << 30)  \
  | (0x0ULL << 31) \
  )

/*! \brief Event select
  
  Events are listed in Table 2-14.  They are defined in detail
  in Section 2.3.7.
  
  To change events to count:
  -# Define event below
  -# Modify events array in intel_hsw_cbo_begin()
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

/*! \name Events

  Events are listed in Table 2-14.  They are defined in detail
  in Section 2.3.7.  Some events can only be counted on
  specific registers.

@{
 */
#define CLOCK_TICKS          CBOX_PERF_EVENT(0x00, 0x00) //!< CTR0-3
#define RxR_OCCUPANCY        CBOX_PERF_EVENT(0x11, 0x01) //!< CTR0
#define COUNTER0_OCCUPANCY   CBOX_PERF_EVENT(0x1F, 0x00) //!< CTR1-3
#define LLC_LOOKUP_DATA_READ CBOX_PERF_EVENT(0x34, 0x03) //!< CTR0-3
#define LLC_LOOKUP_WRITE     CBOX_PERF_EVENT(0x34, 0x05) //!< CTR0-3
#define LLC_LOOKUP_ANY       CBOX_PERF_EVENT(0x34, 0x11) //!< CTR0-3
#define RING_IV_USED         CBOX_PERF_EVENT(0x1E, 0x0F)
//@}

//! Configure and start counters for CBo
static int intel_hsw_cbo_begin_box(char *cpu, int box, uint64_t *events, size_t nr_events)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t ctl;
  uint64_t filter;
  int offset = box*16;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

  ctl = 0x00100ULL; // Freeze (Bit 8)
  /* CBo ctrl registers are 16-bits apart */
  if (pwrite(msr_fd, &ctl, sizeof(ctl), CBOX_CTL0 + offset) < 0) {
    ERROR("cannot enable freeze of CBo counter %d: %m\n",box);
    goto out;
  }

  /* Filtering by opcode, MESIF state, node, core and thread possible */
  filter = CBOX_FILTER0();
  if (pwrite(msr_fd, &filter, sizeof(filter), CBOX_FILTER0_0 + offset) < 0) {
    ERROR("cannot modify CBo Filter 0 : %m\n");
    goto out;
  }
  filter = CBOX_FILTER1();
  if (pwrite(msr_fd, &filter, sizeof(filter), CBOX_FILTER1_0 + offset) < 0) {
    ERROR("cannot modify CBo Filter 1: %m\n");
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
  ctl = 0x00000ULL; // unfreeze counter
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
  RxR_OCCUPANCY, 
  LLC_LOOKUP_DATA_READ, 
  RING_IV_USED, 
  LLC_LOOKUP_WRITE,
};

//! Configure and start counters
static int intel_hsw_cbo_begin(struct stats_type *type)
{
  int nr = 0;

  int i,j;
  if (processor != HASWELL) goto out;

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
	if (intel_hsw_cbo_begin_box(cpu, j, events, 4) == 0)
	  nr++;      
  }
  
 out:
  if (nr == 0)
    type->st_enabled = 0;  
  return nr > 0 ? 0 : -1;
}

//! Collect values in counters for a CBo
static void intel_hsw_cbo_collect_box(struct stats_type *type, char *cpu, int pkg_id, int box)
{
  struct stats *stats = NULL;
  char msr_path[80];
  int msr_fd = -1;
  int offset;
  offset = 16*box;

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
static void intel_hsw_cbo_collect(struct stats_type *type)
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
	intel_hsw_cbo_collect_box(type, cpu, pkg_id, j);
  }
}

//! Definition of stats for this type
struct stats_type intel_hsw_cbo_stats_type = {
  .st_name = "intel_hsw_cbo",
  .st_begin = &intel_hsw_cbo_begin,
  .st_collect = &intel_hsw_cbo_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
