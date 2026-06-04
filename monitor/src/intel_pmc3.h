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
#include "msr_io.h"
#include "cpuid.h"

/*! 
 \file intel_pmc3.h
 \author Todd Evans 
 \brief Counters for Intel Performance Monitoring Version 3

  \par Location of cpu info and monitoring register files:

  ex) Display cpuid and msr file for cpu 0:

      $ ls -l /dev/cpu/0
      total 0
      crw-------  1 root root 203, 0 Oct 28 18:47 cpuid
      crw-------  1 root root 202, 0 Oct 28 18:47 msr


  \par MSR address layout of registers:

  IA32_PMCx (CTRx) MSRs start at address 0C1H and occupy a contiguous block of MSR
  address space; the number of MSRs per logical processor is reported using
  CPUID.0AH:EAX[15:8].  

  IA32_PERFEVTSELx (CTLx) MSRs start at address 186H and occupy a contiguous block
  of MSR address space. Each performance event select register is paired with a
  corresponding performance counter in the 0C1H address block.
*/

#define IA32_CTR0 0xC1 /* CPUID.0AH: EAX[15:8] > 0 */
#define IA32_CTR1 0xC2 /* CPUID.0AH: EAX[15:8] > 1 */
#define IA32_CTR2 0xC3 /* CPUID.0AH: EAX[15:8] > 2 */
#define IA32_CTR3 0xC4 /* CPUID.0AH: EAX[15:8] > 3 */
#define IA32_CTR4 0xC5 /* CPUID.0AH: EAX[15:8] > 4 */
#define IA32_CTR5 0xC6 /* CPUID.0AH: EAX[15:8] > 5 */
#define IA32_CTR6 0xC7 /* CPUID.0AH: EAX[15:8] > 6 */
#define IA32_CTR7 0xC8 /* CPUID.0AH: EAX[15:8] > 7 */

#define IA32_CTL0 0x186 /* CPUID.0AH: EAX[15:8] > 0 */
#define IA32_CTL1 0x187 /* CPUID.0AH: EAX[15:8] > 1 */
#define IA32_CTL2 0x188 /* CPUID.0AH: EAX[15:8] > 2 */
#define IA32_CTL3 0x189 /* CPUID.0AH: EAX[15:8] > 3 */
#define IA32_CTL4 0x18A /* CPUID.0AH: EAX[15:8] > 4 */
#define IA32_CTL5 0x18B /* CPUID.0AH: EAX[15:8] > 5 */
#define IA32_CTL6 0x18C /* CPUID.0AH: EAX[15:8] > 6 */
#define IA32_CTL7 0x18D /* CPUID.0AH: EAX[15:8] > 7 */

/*! \name Fixed Counter Registers

  These counters always count the same events.
  @{
*/
#define IA32_FIXED_CTR_CTRL 0x38D //!< Fixed Counter Control Register
#define IA32_FIXED_CTR0     0x309 //!< Fixed Counter 0: Instructions Retired
#define IA32_FIXED_CTR1     0x30A //!< Fixed Counter 1: Core Clock Cycles
#define IA32_FIXED_CTR2     0x30B //!< Fixed Counter 2: Reference Clock Cycles
//@}

/*! \name Global Control Registers
  
  Controls for all registers.
  @{
*/
#define IA32_PERF_GLOBAL_STATUS   0x38E //!< indicates overflow 
#define IA32_PERF_GLOBAL_CTRL     0x38F //!< enables all fixed and configurable counters
#define IA32_PERF_GLOBAL_OVF_CTRL 0x390 //!< clears overflow indicators in GLOBAL_STATUS.
//@}

/* Schema Keys 
   All counter registers are 48 bits wide. 
*/

#define KEYS \
    X(fp_arith_inst_retired_scalar_double, "E,W=48", ""), \
    X(fp_arith_inst_retired_128b_packed_double, "E,W=48", ""), \
    X(fp_arith_inst_retired_256b_packed_double, "E,W=48", ""), \
    X(fp_arith_inst_retired_512b_packed_double, "E,W=48", ""), \
    X(fp_arith_inst_retired_scalar_single, "E,W=48", ""), \
    X(fp_arith_inst_retired_128b_packed_single, "E,W=48", ""), \
    X(fp_arith_inst_retired_256b_packed_single, "E,W=48", ""), \
    X(fp_arith_inst_retired_512b_packed_single, "E,W=48", ""),	 \
    X(mem_uops_retired_all_loads, "E,W=48", ""), \
    X(mem_load_uops_retired_l1_hit, "E,W=48", ""), \
    X(mem_load_uops_retired_l2_hit, "E,W=48", ""), \
    X(mem_load_uops_retired_llc_hit, "E,W=48", ""), \
    X(l1d_replacement, "E,W=48", ""), \
    X(dtlb_load_misses_miss_causes_a_walk, "E,W=48", ""), \
    X(resource_stalls_any, "E,W=48", ""), \
    X(l2_lines_in_all, "E,W=48", ""), \
    X(mem_uncore_retired_remote_dram, "E,W=48", ""), \
    X(mem_uncore_retired_local_dram, "E,W=48", ""), \
    X(fp_comp_ops_exe_sse_fp_packed, "E,W=48", ""), \
    X(fp_comp_ops_exe_sse_fp_scalar, "E,W=48", ""), \
    X(simd_fp_256_packed_double, "E,W=48", ""), \
    X(instr_retired, "E,W=48", ""), \
    X(aperf, "E,W=48", ""), \
    X(mperf, "E,W=48", "")

#define HT_KEYS KEYS

#define KNL_KEYS		 \
  X(mem_uops_retired_all_loads_knl, "E,W=40", ""),	 \
    X(mem_uops_retired_l2_hit_loads_knl, "E,W=40", ""),	 \
    X(instr_retired, "E,W=40", ""), \
    X(aperf, "E,W=40", ""), \
    X(mperf, "E,W=40", "")

/*! \brief Event select */
// IA32_PERFEVTSELx MSR layout
//   [0, 7] Event Select
//   [8, 15] Unit Mask (UMASK)
//   16 USR
//   17 OS
//   18 E Edge_detect
//   19 PC Pin control
//   20 INT APIC interrupt enable
//   21 ANY Any thread (version 3)
//   22 EN Enable counters
//   23 INV Invert counter mask
//   [24, 31] Counter Mask (CMASK)
//   [32, 63] Reserved
#define PERF_EVENT(event, umask) \
  ( (event)			 \
    | (umask << 8)		 \
    | (1ULL << 16)		 \
    | (1ULL << 17)		 \
    | (0ULL << 21)		 \
    | (1ULL << 22)		 \
    )

/* NHM WTM */
#define MEM_UNCORE_RETIRED_REMOTE_DRAM PERF_EVENT(0x0F, 0x10) /* CHECKME */
#define MEM_UNCORE_RETIRED_LOCAL_DRAM  PERF_EVENT(0x0F, 0x20) /* CHECKME */
#define FP_COMP_OPS_EXE_X87            PERF_EVENT(0x10, 0x01)
#define MEM_LOAD_RETIRED_L1D_HIT       PERF_EVENT(0xCB, 0x01)
#define MEM_LOAD_RETIRED_L2_HIT        PERF_EVENT(0xCB, 0x02)
#define MEM_LOAD_RETIRED_L3_HIT        PERF_EVENT(0xCB, 0x0C)
#define MEM_LOAD_RETIRED_L3_MISS       PERF_EVENT(0xCB, 0x10) /* May be same as 0x0F/0x10 + 0x0F/0x20. */

/* SNB IVB */
#define DTLB_LOAD_MISSES_WALK_CYCLES   PERF_EVENT(0x08, 0x04)
#define FP_COMP_OPS_EXE_SSE_FP_PACKED  PERF_EVENT(0x10, 0x10)
#define FP_COMP_OPS_EXE_SSE_FP_SCALAR  PERF_EVENT(0x10, 0x80)
#define SSE_DOUBLE_SCALAR_PACKED       PERF_EVENT(0x10, 0x90)
#define SIMD_FP_256_PACKED_DOUBLE      PERF_EVENT(0x11, 0x02)

/* KNL */
#define MEM_UOPS_RETIRED_ALL_LOADS_KNL     PERF_EVENT(0x04, 0x40)
#define MEM_UOPS_RETIRED_L2_HIT_LOADS_KNL  PERF_EVENT(0x04, 0x02)

/* SKX CLX */
#define FP_ARITH_INST_RETIRED_SCALAR_DOUBLE      PERF_EVENT(0xC7, 0x01)
#define FP_ARITH_INST_RETIRED_SCALAR_SINGLE      PERF_EVENT(0xC7, 0x02)
#define FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE PERF_EVENT(0xC7, 0x04)
#define FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE PERF_EVENT(0xC7, 0x08)
#define FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE PERF_EVENT(0xC7, 0x10)
#define FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE PERF_EVENT(0xC7, 0x20)
#define FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE PERF_EVENT(0xC7, 0x40)
#define FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE PERF_EVENT(0xC7, 0x80)

/* HSW and later  */
#define DTLB_LOAD_MISSES_MISS_CAUSES_A_WALK PERF_EVENT(0x08, 0x01)
#define DTLB_LOAD_MISSES_WALK_COMPLETED_2M_4M PERF_EVENT(0x08, 0x04)
#define L1D_REPLACEMENT                PERF_EVENT(0x51, 0x01) 
#define RESOURCE_STALLS_ANY            PERF_EVENT(0xA2, 0x01) 
#define MEM_UOPS_RETIRED_ALL_LOADS     PERF_EVENT(0xD0, 0x81) // CTR0-3 Only
#define MEM_UOPS_RETIRED_ALL_STORES    PERF_EVENT(0xD0, 0x82) // CTR0-3 Only
#define MEM_LOAD_UOPS_RETIRED_L1_HIT   PERF_EVENT(0xD1, 0x01) // CTR0-3 Only
#define MEM_LOAD_UOPS_RETIRED_L2_HIT   PERF_EVENT(0xD1, 0x02) // CTR0-3 Only
#define MEM_LOAD_UOPS_RETIRED_LLC_HIT  PERF_EVENT(0xD1, 0x04) // CTR0-3 Only
#define MEM_LOAD_UOPS_RETIRED_LLC_MISS PERF_EVENT(0xD1, 0x20) // CTR0-3 Only
#define MEM_LOAD_UOPS_RETIRED_HIT_LFB  PERF_EVENT(0xD1, 0x40) // CTR0-3 Only
#define MEM_LOAD_UOPS_RETIRED_L1_MISS  PERF_EVENT(0xD1, 0x08)
#define MEM_LOAD_UOPS_RETIRED_L2_MISS  PERF_EVENT(0xD1, 0x10)
#define MEM_LOAD_UOPS_RETIRED_L3_MISS  PERF_EVENT(0xD1, 0x20)
#define MEM_LOAD_UOPS_RETIRED_HIT_LFB  PERF_EVENT(0xD1, 0x40)
#define L2_LINES_IN_ALL                PERF_EVENT(0xF1, 0x07)

static uint64_t skx_events[] = {
  FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,
  FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,
  FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,
  FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,
  FP_ARITH_INST_RETIRED_SCALAR_SINGLE,
  FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,
  FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,
  FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,
};
static  uint64_t knl_events[] = {
  MEM_UOPS_RETIRED_ALL_LOADS_KNL,
  MEM_UOPS_RETIRED_L2_HIT_LOADS_KNL,
};
static  uint64_t hsw_events[] = {
  MEM_UOPS_RETIRED_ALL_LOADS,
  MEM_LOAD_UOPS_RETIRED_L1_HIT,
  MEM_LOAD_UOPS_RETIRED_L2_HIT,
  L1D_REPLACEMENT,
  MEM_LOAD_UOPS_RETIRED_LLC_HIT,
  DTLB_LOAD_MISSES_MISS_CAUSES_A_WALK,
  RESOURCE_STALLS_ANY,
  L2_LINES_IN_ALL,
};
static  uint64_t snb_events[] = {
  MEM_UOPS_RETIRED_ALL_LOADS,
  MEM_LOAD_UOPS_RETIRED_L1_HIT,
  MEM_LOAD_UOPS_RETIRED_L2_HIT,
  MEM_LOAD_UOPS_RETIRED_LLC_HIT,
  L1D_REPLACEMENT,
  FP_COMP_OPS_EXE_SSE_FP_SCALAR,
  FP_COMP_OPS_EXE_SSE_FP_PACKED,
  SIMD_FP_256_PACKED_DOUBLE,
};
static  uint64_t nhm_events[] = {
  MEM_UNCORE_RETIRED_REMOTE_DRAM,
  MEM_UNCORE_RETIRED_LOCAL_DRAM,
  FP_COMP_OPS_EXE_SSE_FP_PACKED,
  FP_COMP_OPS_EXE_SSE_FP_SCALAR
};

static const char *const skx_event_keys[] = {
  "fp_arith_inst_retired_scalar_double",
  "fp_arith_inst_retired_128b_packed_double",
  "fp_arith_inst_retired_256b_packed_double",
  "fp_arith_inst_retired_512b_packed_double",
  "fp_arith_inst_retired_scalar_single",
  "fp_arith_inst_retired_128b_packed_single",
  "fp_arith_inst_retired_256b_packed_single",
  "fp_arith_inst_retired_512b_packed_single",
};
static const char *const knl_event_keys[] = {
  "mem_uops_retired_all_loads_knl",
  "mem_uops_retired_l2_hit_loads_knl",
};
static const char *const hsw_event_keys[] = {
  "mem_uops_retired_all_loads",
  "mem_load_uops_retired_l1_hit",
  "mem_load_uops_retired_l2_hit",
  "l1d_replacement",
  "mem_load_uops_retired_llc_hit",
  "dtlb_load_misses_miss_causes_a_walk",
  "resource_stalls_any",
  "l2_lines_in_all",
};
static const char *const snb_event_keys[] = {
  "mem_uops_retired_all_loads",
  "mem_load_uops_retired_l1_hit",
  "mem_load_uops_retired_l2_hit",
  "mem_load_uops_retired_llc_hit",
  "l1d_replacement",
  "fp_comp_ops_exe_sse_fp_scalar",
  "fp_comp_ops_exe_sse_fp_packed",
  "simd_fp_256_packed_double",
};
static const char *const nhm_event_keys[] = {
  "mem_uncore_retired_remote_dram",
  "mem_uncore_retired_local_dram",
  "fp_comp_ops_exe_sse_fp_packed",
  "fp_comp_ops_exe_sse_fp_scalar",
};

static const char *const *intel_pmc3_event_keys(size_t *nkeys)
{
  const char *const *keys = NULL;
  size_t n = 0;

  switch (processor) {
  case NEHALEM:
  case WESTMERE:
    keys = nhm_event_keys; n = sizeof(nhm_event_keys) / sizeof(nhm_event_keys[0]); break;
  case SANDYBRIDGE:
  case IVYBRIDGE:
    keys = snb_event_keys; n = sizeof(snb_event_keys) / sizeof(snb_event_keys[0]); break;
  case HASWELL:
  case BROADWELL:
    keys = hsw_event_keys; n = sizeof(hsw_event_keys) / sizeof(hsw_event_keys[0]); break;
  case KNL:
    keys = knl_event_keys; n = sizeof(knl_event_keys) / sizeof(knl_event_keys[0]); break;
  case SKYLAKE:
    keys = skx_event_keys; n = sizeof(skx_event_keys) / sizeof(skx_event_keys[0]); break;
  default:
    break;
  }
  if (nkeys != NULL)
    *nkeys = n;
  return keys;
}

//! Generate bitmask of n 1s
#define BIT_MASK(n) (~( ((~0ULL) << ((n)-1)) << 1 ))

//! Configure and start counters for a pmc3 cpu counters
static int intel_pmc3_begin_cpu(char *cpu)
{
  int rc = -1;
  int msr_fd = -1;
  uint64_t global_ctr_ctrl, fixed_ctr_ctrl;
  uint64_t *events;

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
    ERROR("Processor model/family not supported: %m\n");
    goto out;
  }

  msr_fd = msr_open_cpu(cpu, O_RDWR);
  if (msr_fd < 0)
    goto out;

  /* Disable counters globally. */
  global_ctr_ctrl = 0x0ULL;
  if (msr_write_u64(msr_fd, IA32_PERF_GLOBAL_CTRL, global_ctr_ctrl) < 0) {
    ERROR("cannot disable performance counters: %m\n");
    goto out;
  }

  int i;
  for (i = 0; i < n_pmcs; i++) {
    TRACE("MSR %08X, event %016llX\n", IA32_CTL0 + i, (unsigned long long) events[i]);
    if (msr_write_u64(msr_fd, IA32_CTL0 + i, events[i]) < 0) {
      ERROR("cannot write event %016llX to MSR %08X for cpu `%s': %m\n",
            (unsigned long long) events[i],
            (unsigned) IA32_CTL0 + i,
            cpu);
      goto out;
    }
  }

  rc = 0;
  /* Enable fixed counters.  Three 4 bit blocks, enable OS, User, Turn off any thread. */
  fixed_ctr_ctrl = 0x333UL;

  if (msr_write_u64(msr_fd, IA32_FIXED_CTR_CTRL, fixed_ctr_ctrl) < 0)
    ERROR("cannot enable fixed counters: %m\n");

  /* Enable counters globally, n_pmcs PMC and 3 fixed. */
  global_ctr_ctrl = BIT_MASK(n_pmcs) | (0x7ULL << 32);
  if (msr_write_u64(msr_fd, IA32_PERF_GLOBAL_CTRL, global_ctr_ctrl) < 0)
    ERROR("cannot enable performance counters: %m\n");

 out:
  if (msr_fd >= 0)
    close(msr_fd);

  return rc;
}

static int intel_pmc3_begin(struct stats_type *type)
{
  int nr = 0;
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
