/*!
 \file intel_knl_mc.c
 \author Todd Evans
 \brief Performance Monitoring Counters for Intel Knights Landing DRAM MC
*/

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>
#include "cpuid.h"
#include "stats.h"
#include "trace.h"
#include "intel_mmconfig.h"
#include "intel_uncore_mmio.h"

#define pci_cfg_address(bus, dev, func) (((bus) << 20) | ((dev) << 15) | ((func) << 12))

#define UCLK_PMON_UNIT_CTL_REG    0x430
#define UCLK_PMON_UNIT_STATUS_REG 0x434

#define UCLK_PMON_CTR0_LOW_REG  0x400
#define UCLK_PMON_CTR0_HIGH_REG 0x404
#define UCLK_PMON_CTR1_LOW_REG  0x408
#define UCLK_PMON_CTR1_HIGH_REG 0x40C
#define UCLK_PMON_CTR2_LOW_REG  0x410
#define UCLK_PMON_CTR2_HIGH_REG 0x414
#define UCLK_PMON_CTR3_LOW_REG  0x418
#define UCLK_PMON_CTR3_HIGH_REG 0x41C

#define UCLK_PMON_CTRCTL0_REG   0x420

#define DCLK_PMON_UNIT_CTL_REG    0xB30
#define DCLK_PMON_UNIT_STATUS_REG 0xB34

#define DCLK_PMON_CTR0_LOW_REG  0xB00
#define DCLK_PMON_CTR0_HIGH_REG 0xB04
#define DCLK_PMON_CTR1_LOW_REG  0xB08
#define DCLK_PMON_CTR1_HIGH_REG 0xB0C
#define DCLK_PMON_CTR2_LOW_REG  0xB10
#define DCLK_PMON_CTR2_HIGH_REG 0xB14
#define DCLK_PMON_CTR3_LOW_REG  0xB18
#define DCLK_PMON_CTR3_HIGH_REG 0xB1C

#define DCLK_PMON_CTRCTL0_REG   0xB20

#define KEYS                                                              \
	X(dram_cas_reads, "E,W=48", ""),                                                \
	    X(dram_cas_writes, "E,W=48", ""), X(dclk_cycles, "E,W=48", ""), X(uclk_cycles, "E,W=48", "")

#define PERF_EVENT(event, umask)                                              \
	((event) | ((umask) << 8) | (0UL << 17) | (0UL << 18)                  \
	 | (0UL << 20) | (1UL << 22) | (0UL << 23) | (0x0UL << 24))

#define UCLK_CYCLES PERF_EVENT(0x00, 0x00)
#define CAS_READS   PERF_EVENT(0x03, 0x01)
#define CAS_WRITES  PERF_EVENT(0x03, 0x02)
#define DCLK_CYCLES PERF_EVENT(0x00, 0x00)

#define BUS 0xFF

static const unsigned knl_mc_uclk_ctr_lo[] = {
	UCLK_PMON_CTR0_LOW_REG,
	UCLK_PMON_CTR1_LOW_REG,
	UCLK_PMON_CTR2_LOW_REG,
	UCLK_PMON_CTR3_LOW_REG,
};
static const unsigned knl_mc_uclk_ctr_hi[] = {
	UCLK_PMON_CTR0_HIGH_REG,
	UCLK_PMON_CTR1_HIGH_REG,
	UCLK_PMON_CTR2_HIGH_REG,
	UCLK_PMON_CTR3_HIGH_REG,
};
static const unsigned knl_mc_dclk_ctr_lo[] = {
	DCLK_PMON_CTR0_LOW_REG,
	DCLK_PMON_CTR1_LOW_REG,
	DCLK_PMON_CTR2_LOW_REG,
	DCLK_PMON_CTR3_LOW_REG,
};
static const unsigned knl_mc_dclk_ctr_hi[] = {
	DCLK_PMON_CTR0_HIGH_REG,
	DCLK_PMON_CTR1_HIGH_REG,
	DCLK_PMON_CTR2_HIGH_REG,
	DCLK_PMON_CTR3_HIGH_REG,
};
static const char *const knl_mc_uclk_keys[4] = {
	"uclk_cycles", NULL, NULL, NULL
};
static const char *const knl_mc_dclk_keys[4] = {
	"dram_cas_reads", "dram_cas_writes", "dclk_cycles", NULL
};

static void intel_knl_mc_uclk_begin_dev(uint32_t dev, uint32_t *map_dev,
					uint32_t *events, int nr_events)
{
	uint32_t pci = pci_cfg_address(BUS, dev, 0x00);

	intel_uncore_mmio_bank_program(map_dev, pci, UCLK_PMON_UNIT_CTL_REG,
				       UCLK_PMON_UNIT_STATUS_REG,
				       UCLK_PMON_CTRCTL0_REG, events,
				       nr_events);
}

static void intel_knl_mc_dclk_begin_dev(uint32_t dev, uint32_t func,
					uint32_t *map_dev, uint32_t *events,
					int nr_events)
{
	uint32_t pci = pci_cfg_address(BUS, dev, func);

	intel_uncore_mmio_bank_program(map_dev, pci, DCLK_PMON_UNIT_CTL_REG,
				       DCLK_PMON_UNIT_STATUS_REG,
				       DCLK_PMON_CTRCTL0_REG, events,
				       nr_events);
}

static void intel_knl_mc_uclk_collect_dev(struct stats_type *type,
					  uint32_t dev, uint32_t *map_dev)
{
	char dev_str[80];
	uint32_t pci = pci_cfg_address(BUS, dev, 0x00);

	snprintf(dev_str, sizeof(dev_str), "%02x/%02x.0", BUS, dev);
	TRACE("dev %s\n", dev_str);

	intel_uncore_mmio_bank_collect(type, dev_str, pci, map_dev,
				       knl_mc_uclk_keys, UCLK_PMON_CTRCTL0_REG, knl_mc_uclk_ctr_lo,
				       knl_mc_uclk_ctr_hi);
}

static void intel_knl_mc_dclk_collect_dev(struct stats_type *type,
					  uint32_t func, uint32_t dev,
					  uint32_t *map_dev)
{
	char dev_str[80];
	uint32_t pci = pci_cfg_address(BUS, dev, func);

	snprintf(dev_str, sizeof(dev_str), "%02x/%02x.%x", BUS, dev, func);
	TRACE("dev %s\n", dev_str);

	intel_uncore_mmio_bank_collect(type, dev_str, pci, map_dev,
				       knl_mc_dclk_keys, DCLK_PMON_CTRCTL0_REG, knl_mc_dclk_ctr_lo,
				       knl_mc_dclk_ctr_hi);
}

static const int nr_mc_devs = 2;
static uint32_t mc_uclk_events[] = {UCLK_CYCLES};
static const int nr_mc_uclk_events = 1;
static const uint32_t mc_uclk_dev[] = {0x0a, 0x0b};
static uint32_t mc_dclk_events[] = {CAS_READS, CAS_WRITES, DCLK_CYCLES};
static const int nr_mc_dclk_events = 3;
static const uint32_t mc_dclk_dev[] = {0x08, 0x09};

static const uint64_t knl_mc_mmconfig_base = 0xc0000000;
static const uint64_t knl_mc_mmconfig_size = 0x10000000;

static int intel_knl_mc_begin(struct stats_type *type)
{
	int nr = 0;
	struct intel_mmconfig mm = {-1, MAP_FAILED, 0, 0};
	int i;

	if (processor != KNL)
		goto out;
	if (intel_mmconfig_open(&mm, knl_mc_mmconfig_base,
				knl_mc_mmconfig_size) < 0)
		goto out;

	for (i = 0; i < nr_mc_devs; i++) {
		intel_knl_mc_uclk_begin_dev(mc_uclk_dev[i], mm.map,
					    mc_uclk_events,
					    nr_mc_uclk_events);
		nr++;
		intel_knl_mc_dclk_begin_dev(mc_dclk_dev[i], 0x02, mm.map,
					    mc_dclk_events,
					    nr_mc_dclk_events);
		nr++;
		intel_knl_mc_dclk_begin_dev(mc_dclk_dev[i], 0x03, mm.map,
					    mc_dclk_events,
					    nr_mc_dclk_events);
		nr++;
		intel_knl_mc_dclk_begin_dev(mc_dclk_dev[i], 0x04, mm.map,
					    mc_dclk_events,
					    nr_mc_dclk_events);
		nr++;
	}

out:
	intel_mmconfig_close(&mm);
	if (nr == 0)
		type->st_enabled = 0;
	return nr > 0 ? 0 : -1;
}

static void intel_knl_mc_collect(struct stats_type *type)
{
	struct intel_mmconfig mm = {-1, MAP_FAILED, 0, 0};
	int i;

	if (intel_mmconfig_open(&mm, knl_mc_mmconfig_base,
				knl_mc_mmconfig_size) < 0)
		goto out;

	for (i = 0; i < nr_mc_devs; i++) {
		intel_knl_mc_uclk_collect_dev(type, mc_uclk_dev[i], mm.map);
		intel_knl_mc_dclk_collect_dev(type, 0x02, mc_dclk_dev[i],
					      mm.map);
		intel_knl_mc_dclk_collect_dev(type, 0x03, mc_dclk_dev[i],
					      mm.map);
		intel_knl_mc_dclk_collect_dev(type, 0x04, mc_dclk_dev[i],
					      mm.map);
	}

out:
	intel_mmconfig_close(&mm);
}

struct stats_type intel_knl_mc_stats_type = {
    .st_name = "intel_x86_uncore_mc_knl",
    .st_begin = &intel_knl_mc_begin,
    .st_collect = &intel_knl_mc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
