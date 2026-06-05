/*! \file amd64_pmc.h
 *  AMD x86 core PMU MSR definitions and emitted KEYS for amd_x86_pmc.
 *  DF uncore channel keys (DF_KEYS) are consumed by amd_x86_uncore_df.
 *
 *  MSR_PERF_* symbols name hardware registers; emitted schema keys use the
 *  portable names in KEYS / DF_KEYS (instr_retired, dram_chan0_bytes, etc.).
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
#include "cpuid.h"


// From https://developer.amd.com/resources/developer-guides-manuals/
// Open-Source Register Reference for AMD Family 17h Processors
//
// CTLs p. 136 
// MSRC001_020[0...A] [Performance Event Select [5:0]] (Core::X86::Msr::PERF_CTL)
//
// CTRs p. 138
// MSRC001_020[1...B] [Performance Event Counter [5:0]] (Core::X86::Msr::PERF_CTR)

// Event

#define MSR_PERF_CTL0 0xC0010200
#define MSR_PERF_CTL1 0xC0010202
#define MSR_PERF_CTL2 0xC0010204
#define MSR_PERF_CTL3 0xC0010206
#define MSR_PERF_CTL4 0xC0010208
#define MSR_PERF_CTL5 0xC001020A

#define MSR_PERF_CTR0 0xC0010201
#define MSR_PERF_CTR1 0xC0010203
#define MSR_PERF_CTR2 0xC0010205
#define MSR_PERF_CTR3 0xC0010207
#define MSR_PERF_CTR4 0xC0010209
#define MSR_PERF_CTR5 0xC001020B

#define MSR_PERF_INST_RETIRED  0xC00000E9
#define MSR_PERF_APERF         0xC00000E8
#define MSR_PERF_MPERF         0xC00000E7

#define MSR_HW_CONFIG   0xC0010015


// SSE/AVX operations p. 151
// PMCx003 [Retired SSE/AVX Operations] (Core::X86::Pmc::Core::FpRetSseAvxOps)
// Cannot separate single or double precision flops in epyc2 even though manual says otherwise
#define FLOPS_SINGLE             PERF_EVENT(0x03, 0x0F) /* Counts single precision, add, multiply, divide & sqrt FLOPs. */
#define FLOPS_DOUBLE             PERF_EVENT(0x03, 0xF0) /* Counts double precision, add, multiply, divide & sqrt FLOPs. */

#define FLOPS                    PERF_EVENT(0x03, 0xFF) //0x43FF03
#define MERGE                    0xF004000FF  // FLOPS need merge because events incr > 16 / cycle
#define BRANCH_INST_RETIRED      PERF_EVENT(0xC2, 0x00)
#define BRANCH_INST_RETIRED_MISS PERF_EVENT(0xC3, 0x00)
#define DISPATCH_STALL_CYCLES1   PERF_EVENT(0xAF, 0x08) 
#define DISPATCH_STALL_CYCLES0   PERF_EVENT(0xAE, 0xFF) 

#define KEYS \
  X(fp_ops_retired, "E,W=48", ""), \
  X(fp_ops_merge, "E,W=48", ""), \
  X(branch_inst_retired, "E,W=48", ""), \
  X(branch_inst_retired_miss, "E,W=48", ""), \
  X(dispatch_stall_cycles1, "E,W=48", ""), \
  X(dispatch_stall_cycles0, "E,W=48", ""), \
  X(instr_retired, "E,W=48", ""), \
  X(aperf, "E,W=48", ""), \
  X(mperf, "E,W=48", "")

#define DF_KEYS \
  X(dram_chan0_bytes, "E,W=48", ""), \
  X(dram_chan1_bytes, "E,W=48", ""), \
  X(dram_chan2_bytes, "E,W=48", ""), \
  X(dram_chan3_bytes, "E,W=48", "")


#define PERF_EVENT(event_select, unit_mask) \
  ( (event_select & 0xFF) \
  | (unit_mask << 8) \
  | (1UL << 16) /* Count in user mode (CPL == 0). */ \
  | (1UL << 17) /* Count in OS mode (CPL > 0). */ \
  | (1UL << 22) /* Enable. */ \
  )



/* LEGACY 10H Counters and Events
 * Left in case 10H needs to be added back


// The performance monitor counters are used by software to count
// specific events that occur in the processor.  [The Performance
// Event Select Register (PERF_CTL[3:0])] MSRC001_00[03:00] and [The
// Performance Event Counter Registers (PERF_CTR[3:0])]
// MSRC001_00[07:04] specify the events to be monitored and how they
// are monitored.  All of the events are specified in section 3.14
// [Performance Counter Events].
//
// In 17H these CTR/CTLs are valid but legacy and just pointers
// to the real registers, however, 17H has an additional 2 
// counters. This is left here in case 10H needs to be supported 
// again (need 10H sig)

#define LEGACY_MSR_PERF_CTL0 0xC0010000
#define LEGACY_MSR_PERF_CTL1 0xC0010001
#define LEGACY_MSR_PERF_CTL2 0xC0010002
#define LEGACY_MSR_PERF_CTL3 0xC0010003
#define LEGACY_MSR_PERF_CTR0 0xC0010004
#define LEGACY_MSR_PERF_CTR1 0xC0010005
#define LEGACY_MSR_PERF_CTR2 0xC0010006
#define LEGACY_MSR_PERF_CTR3 0xC0010007
*/

/* From the 10h BKDG, p. 403, "The performance counter registers can
v   be used to track events in the Northbridge. Northbridge events
   include all memory controller events, crossbar events, and
   HyperTransportTM interface events as documented in 3.14.7, 3.14.8,
   and 3.14.9. Monitoring of Northbridge events should only be
   performed by one core.  If a Northbridge event is selected using
   one of the Performance Event-Select registers in any core of a
   multi-core processor, then a Northbridge performance event cannot
   be selected in the same Performance Event Select register of any
   other core. */

/* Northbridge events. */
//#define DRAMaccesses   PERF_EVENT(0xE0, 0x07) /* DCT0 only */
//#define HTlink0Use     PERF_EVENT(0xF6, 0x37) /* Counts all except NOPs */
//#define HTlink1Use     PERF_EVENT(0xF7, 0x37) /* Counts all except NOPs */
//#define HTlink2Use     PERF_EVENT(0xF8, 0x37) /* Counts all except NOPs */
/* Core events. */
//#define UserCycles    (PERF_EVENT(0x76, 0x00) & ~(1UL << 17))
//#define DCacheSysFills PERF_EVENT(0x42, 0x01) /* Counts DCache fills from beyond the L2 cache. */
//#define SSEFLOPS       PERF_EVENT(0x03, 0x7F) /* Counts single & double, add, multiply, divide & sqrt FLOPs. */


