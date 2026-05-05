/*!
 \file intel_knl_edc.c
 \author Todd Evans
 \brief Performance Monitoring Counters for Intel Knights Landing EDC
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

#define UCLK_PMON_UNIT_CTL_REG       0x430
#define UCLK_PMON_UNIT_STATUS_REG    0x434

#define UCLK_PMON_CTR0_LOW_REG  0x400
#define UCLK_PMON_CTR0_HIGH_REG 0x404
#define UCLK_PMON_CTR1_LOW_REG  0x408
#define UCLK_PMON_CTR1_HIGH_REG 0x40C
#define UCLK_PMON_CTR2_LOW_REG  0x410
#define UCLK_PMON_CTR2_HIGH_REG 0x414
#define UCLK_PMON_CTR3_LOW_REG  0x418
#define UCLK_PMON_CTR3_HIGH_REG 0x41C

#define UCLK_PMON_CTRCTL0_REG   0x420

#define ECLK_PMON_UNIT_CTL_REG       0xA30
#define ECLK_PMON_UNIT_STATUS_REG    0xA34

#define ECLK_PMON_CTR0_LOW_REG  0xA00
#define ECLK_PMON_CTR0_HIGH_REG 0xA04
#define ECLK_PMON_CTR1_LOW_REG  0xA08
#define ECLK_PMON_CTR1_HIGH_REG 0xA0C
#define ECLK_PMON_CTR2_LOW_REG  0xA10
#define ECLK_PMON_CTR2_HIGH_REG 0xA14
#define ECLK_PMON_CTR3_LOW_REG  0xA18
#define ECLK_PMON_CTR3_HIGH_REG 0xA1C

#define ECLK_PMON_CTRCTL0_REG   0xA20

#define CTL_KEYS                                                              \
	X(CTL0, "C", ""),                                                     \
	    X(CTL1, "C", ""), X(CTL2, "C", ""), X(CTL3, "C", "")
#define CTR_KEYS                                                              \
	X(CTR0, "E,W=48", ""),                                                \
	    X(CTR1, "E,W=48", ""), X(CTR2, "E,W=48", ""), X(CTR3, "E,W=48", "")

#define KEYS CTL_KEYS CTR_KEYS

#define PERF_EVENT(event, umask)                                              \
	((event) | ((umask) << 8) | (0UL << 17) | (0UL << 18) /* Edge */       \
	 | (0UL << 20) /* Overflow disable */ | (1UL << 22) /* Enable. */     \
	 | (0UL << 23) /* Invert */ | (0x0UL << 24) /* Threshold */)

#define EDC_HIT_CLEAN  PERF_EVENT(0x02, 0x01)
#define EDC_HIT_DIRTY  PERF_EVENT(0x02, 0x02)
#define EDC_MISS_CLEAN PERF_EVENT(0x02, 0x04)
#define EDC_MISS_DIRTY PERF_EVENT(0x02, 0x08)

#define RPQ_INSERTS PERF_EVENT(0x01, 0x01)
#define WPQ_INSERTS PERF_EVENT(0x02, 0x01)
#define ECLK_CYCLES PERF_EVENT(0x00, 0x00)

#define BUS 0xFF

static const unsigned knl_uclk_ctr_lo[] = {
	UCLK_PMON_CTR0_LOW_REG,
	UCLK_PMON_CTR1_LOW_REG,
	UCLK_PMON_CTR2_LOW_REG,
	UCLK_PMON_CTR3_LOW_REG,
};
static const unsigned knl_uclk_ctr_hi[] = {
	UCLK_PMON_CTR0_HIGH_REG,
	UCLK_PMON_CTR1_HIGH_REG,
	UCLK_PMON_CTR2_HIGH_REG,
	UCLK_PMON_CTR3_HIGH_REG,
};
static const unsigned knl_eclk_ctr_lo[] = {
	ECLK_PMON_CTR0_LOW_REG,
	ECLK_PMON_CTR1_LOW_REG,
	ECLK_PMON_CTR2_LOW_REG,
	ECLK_PMON_CTR3_LOW_REG,
};
static const unsigned knl_eclk_ctr_hi[] = {
	ECLK_PMON_CTR0_HIGH_REG,
	ECLK_PMON_CTR1_HIGH_REG,
	ECLK_PMON_CTR2_HIGH_REG,
	ECLK_PMON_CTR3_HIGH_REG,
};

static void intel_knl_edc_uclk_begin_dev(uint32_t dev, uint32_t *map_dev,
					 uint32_t *events, int nr_events)
{
	uint32_t pci = pci_cfg_address(BUS, dev, 0x00);

	intel_uncore_mmio_bank_program(map_dev, pci, UCLK_PMON_UNIT_CTL_REG,
				       UCLK_PMON_UNIT_STATUS_REG,
				       UCLK_PMON_CTRCTL0_REG, events,
				       nr_events);
}

static void intel_knl_edc_eclk_begin_dev(uint32_t dev, uint32_t *map_dev,
					 uint32_t *events, int nr_events)
{
	uint32_t pci = pci_cfg_address(BUS, dev, 0x02);

	intel_uncore_mmio_bank_program(map_dev, pci, ECLK_PMON_UNIT_CTL_REG,
				       ECLK_PMON_UNIT_STATUS_REG,
				       ECLK_PMON_CTRCTL0_REG, events,
				       nr_events);
}

static void intel_knl_edc_uclk_collect_dev(struct stats_type *type,
					   uint32_t dev, uint32_t *map_dev)
{
	char dev_str[80];
	uint32_t pci = pci_cfg_address(BUS, dev, 0x00);

	snprintf(dev_str, sizeof(dev_str), "%02x/%02x.0", BUS, dev);
	TRACE("dev %s\n", dev_str);

	intel_uncore_mmio_bank_collect(type, dev_str, pci, map_dev,
				       UCLK_PMON_CTRCTL0_REG, knl_uclk_ctr_lo,
				       knl_uclk_ctr_hi);
}

static void intel_knl_edc_eclk_collect_dev(struct stats_type *type,
					   uint32_t dev, uint32_t *map_dev)
{
	char dev_str[80];
	uint32_t pci = pci_cfg_address(BUS, dev, 0x02);

	snprintf(dev_str, sizeof(dev_str), "%02x/%02x.2", BUS, dev);
	TRACE("dev %s\n", dev_str);

	intel_uncore_mmio_bank_collect(type, dev_str, pci, map_dev,
				       ECLK_PMON_CTRCTL0_REG, knl_eclk_ctr_lo,
				       knl_eclk_ctr_hi);
}

static const int nr_edc_devs = 8;
static uint32_t edc_uclk_events[] = {
	EDC_HIT_CLEAN,
	EDC_HIT_DIRTY,
	EDC_MISS_CLEAN,
	EDC_MISS_DIRTY,
};
static const int nr_edc_uclk_events = 4;
static const uint32_t edc_uclk_dev[] = {0x0f, 0x10, 0x11, 0x12,
				       0x13, 0x14, 0x15, 0x16};
static uint32_t edc_eclk_events[] = {
	RPQ_INSERTS,
	WPQ_INSERTS,
	ECLK_CYCLES,
};
static const int nr_edc_eclk_events = 3;
static const uint32_t edc_eclk_dev[] = {0x18, 0x19, 0x1a, 0x1b,
				       0x1c, 0x1d, 0x1e, 0x1f};

static const uint64_t knl_mmconfig_base = 0xc0000000;
static const uint64_t knl_mmconfig_size = 0x10000000;

static int intel_knl_edc_begin(struct stats_type *type)
{
	int nr = 0;
	struct intel_mmconfig mm = {-1, MAP_FAILED, 0, 0};
	int i;

	if (processor != KNL)
		goto out;

	if (intel_mmconfig_open(&mm, knl_mmconfig_base, knl_mmconfig_size) <
	    0)
		goto out;

	for (i = 0; i < nr_edc_devs; i++) {
		intel_knl_edc_uclk_begin_dev(edc_uclk_dev[i], mm.map,
					     edc_uclk_events,
					     nr_edc_uclk_events);
		nr++;
		intel_knl_edc_eclk_begin_dev(edc_eclk_dev[i], mm.map,
					     edc_eclk_events,
					     nr_edc_eclk_events);
		nr++;
	}

out:
	intel_mmconfig_close(&mm);
	if (nr == 0)
		type->st_enabled = 0;
	return nr > 0 ? 0 : -1;
}

static void intel_knl_edc_collect(struct stats_type *type)
{
	struct intel_mmconfig mm = {-1, MAP_FAILED, 0, 0};
	int i;

	if (intel_mmconfig_open(&mm, knl_mmconfig_base, knl_mmconfig_size) <
	    0)
		goto out;

	for (i = 0; i < nr_edc_devs; i++) {
		intel_knl_edc_uclk_collect_dev(type, edc_uclk_dev[i],
					       mm.map);
		intel_knl_edc_eclk_collect_dev(type, edc_eclk_dev[i],
					       mm.map);
	}

out:
	intel_mmconfig_close(&mm);
}

struct stats_type intel_knl_edc_stats_type = {
    .st_name = "intel_knl_edc",
    .st_begin = &intel_knl_edc_begin,
    .st_collect = &intel_knl_edc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
