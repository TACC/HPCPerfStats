/*! 
 \file intel_skx_cha.c
 \author Todd Evans 
 \brief Performance Monitoring Counters for Intel Skylake Caching Home Agent (CHA)

  \par Location of cpu info and monitoring register files:

  ex) Display cpuid and msr file for cpu 0:

      $ ls -l /dev/cpu/0
      total 0
      crw-------  1 root root 203, 0 Oct 28 18:47 cpuid
      crw-------  1 root root 202, 0 Oct 28 18:47 msr

   There are 4 configure, 4 counter, 1  CHA global control, 
   and 1 CHA filter registers per CHA
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

/*! \name CHA Performance Monitoring Global Control Registers
   @{
*/

#define CHA_CTL0 0xE00

//@}

/*! \name CHA filter registers
@{
 */

#define CHA_FILTER0_0 0xE05
#define CHA_FILTER1_0 0xE06

//@}

/*! \name CBo Performance Monitoring Registers

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
  These are 64 bit but counters are only 48 bits wide.

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
#define KEYS		   \
  X(CTL0, "C", ""),	   \
    X(CTL1, "C", ""),	   \
    X(CTL2, "C", ""),	   \
    X(CTL3, "C", ""),	   \
    X(CTR0, "E,W=48", ""), \
    X(CTR1, "E,W=48", ""), \
    X(CTR2, "E,W=48", ""), \
    X(CTR3, "E,W=48", "")

/*! \brief Event select
    
  To change events to count:
  -# Define event below
  -# Modify events array in intel_hsw_cbo_begin()
*/
#define CHA_PERF_EVENT(event, umask) \
  ( (event)			     \
    | (umask << 8)		     \
    | (0x4 << 20)		     \
  )

/*! \name Events

@{
 */
#define SF_EVICTIONS_MES           CHA_PERF_EVENT(0x3d, 0x07)
#define LLC_LOOKUP_DATA_READ_LOCAL CHA_PERF_EVENT(0x34, 0x33)
#define BYPASS_CHA_IMC_ALL         CHA_PERF_EVENT(0x57, 0x07)
#define LLC_LOOKUP_WRITE           CHA_PERF_EVENT(0x34, 0x05)

//@}

//! Configure and start counters for CHA
static int intel_skx_cha_begin_box(char *cpu, int box, uint64_t *events, size_t nr_events)
{
  int rc = -1;
  char msr_path[80];
  int msr_fd = -1;
  uint64_t ctl;
  uint64_t filter;
  int offset = 16*box;

  snprintf(msr_path, sizeof(msr_path), "/dev/cpu/%s/msr", cpu);
  msr_fd = open(msr_path, O_RDWR);
  if (msr_fd < 0) {
    ERROR("cannot open `%s': %m\n", msr_path);
    goto out;
  }

  ctl = 1ULL << 61; // Un-freeze (Bit 61)
  if (pwrite(msr_fd, &ctl, sizeof(ctl), 0x0700) < 0) {
    ERROR("cannot enable freeze of CHA counter %d: %m\n", box);
    goto out;
  }

  ctl = 0x00100ULL; // Freeze (Bit 8)
  /* CHA ctrl registers are 16-bits apart */
  if (pwrite(msr_fd, &ctl, sizeof(ctl), CHA_CTL0 + offset) < 0) {
    ERROR("cannot enable freeze of CHA counter %d: %m\n", box);
    goto out;
  }

  /* Filtering by opcode, MESIF state, node, core and thread possible */
  /* This setting records all accesses */
  filter = 0x01e20000;
  if (pwrite(msr_fd, &filter, sizeof(filter), CHA_FILTER0_0 + offset) < 0) {
    ERROR("cannot modify CHA Filter 0 : %m\n");
    goto out;
  }
  filter = 0x3b;
  if (pwrite(msr_fd, &filter, sizeof(filter), CHA_FILTER1_0 + offset) < 0) {
    ERROR("cannot modify CHA Filter 1: %m\n");
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
  if (pwrite(msr_fd, &ctl, sizeof(ctl), CHA_CTL0 + offset) < 0) {
    ERROR("cannot unfreeze CBo counters: %m\n");
    goto out;
  }
  
  rc = 0;

 out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

#define pci_cfg_address(bus, dev, func) (bus << 20) | (dev << 15) | (func << 12)
#define index(address, off) (address | off)/4

//! Configure and start counters
static int intel_skx_cha_begin(struct stats_type *type)
{

  int n_pmcs = 0;
  int nr = 0;
  uint64_t events[] = {
    SF_EVICTIONS_MES,
    LLC_LOOKUP_DATA_READ_LOCAL,
    BYPASS_CHA_IMC_ALL,   
    LLC_LOOKUP_WRITE,  
  };

  int i,j;
  if (processor == SKYLAKE)
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
	  if (intel_skx_cha_begin_box(cpu, j, events, 4) == 0)
	    nr++;      
    }

 out:
  if (nr == 0)
    type->st_enabled = 0;  
  return nr > 0 ? 0 : -1;
}

//! Collect values in counters for a CBo
static void intel_skx_cha_collect_box(struct stats_type *type, char *cpu, int pkg_id, int box)
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
static void intel_skx_cha_collect(struct stats_type *type)
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
	intel_skx_cha_collect_box(type, cpu, pkg_id, j);
  }
}

//! Definition of stats for this type
struct stats_type intel_skx_cha_stats_type = {
  .st_name = "intel_skx_cha",
  .st_begin = &intel_skx_cha_begin,
  .st_collect = &intel_skx_cha_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
