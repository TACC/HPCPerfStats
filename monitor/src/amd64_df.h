/*! \file amd64_df.h
 *  AMD data-fabric uncore PMU (amd_x86_uncore_df).
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

#define MSR_DF_CTL0   0xC0010240
#define MSR_DF_CTL1   0xC0010242
#define MSR_DF_CTL2   0xC0010244
#define MSR_DF_CTL3   0xC0010246

#define MSR_DF_CTR0   0xC0010241
#define MSR_DF_CTR1   0xC0010243
#define MSR_DF_CTR2   0xC0010245
#define MSR_DF_CTR3   0xC0010247


// L3 Event Core::X86::Msr::ChL3Pmc


//Data Fabric(DF) Event Core::X86::Msr::DF_PERF_CTL

// RAPL Core::X86::Msr::RAPL_PWR_UNIT

#define EVENT_DRAM_CHANNEL_0     PERF_EVENT(0x07, 0x38)
#define EVENT_DRAM_CHANNEL_1     PERF_EVENT(0x47, 0x38)
#define EVENT_DRAM_CHANNEL_2     PERF_EVENT(0x87, 0x38)
#define EVENT_DRAM_CHANNEL_3     PERF_EVENT(0xC7, 0x38)
#define EVENT_DRAM_CHANNEL_4     PERF_EVENT(0x107, 0x38)
#define EVENT_DRAM_CHANNEL_5     PERF_EVENT(0x147, 0x38)
#define EVENT_DRAM_CHANNEL_6     PERF_EVENT(0x187, 0x38)
#define EVENT_DRAM_CHANNEL_7     PERF_EVENT(0x1C7, 0x38)


#define KEYS \
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

