#include "intel_uncore_mmio.h"
#include "stats.h"
#include <stdio.h>

unsigned intel_uncore_mmio_word_index(uint32_t pci_cfg_base, uint32_t byte_off)
{
	return (unsigned)((pci_cfg_base | byte_off) / 4u);
}

void intel_uncore_mmio_bank_program(uint32_t *map_dev, uint32_t pci_cfg_base,
				    unsigned unit_ctl_byte_off,
				    unsigned unit_status_byte_off,
				    unsigned ctrctl0_byte_off,
				    const uint32_t *events, int nr_events)
{
	unsigned wi;
	int i;
	uint32_t ctl = 0;

	wi = intel_uncore_mmio_word_index(pci_cfg_base, unit_ctl_byte_off);
	map_dev[wi] = ctl;
	wi = intel_uncore_mmio_word_index(pci_cfg_base, unit_status_byte_off);
	map_dev[wi] = ctl;

	for (i = 0; i < nr_events; i++) {
		wi = intel_uncore_mmio_word_index(pci_cfg_base,
						  ctrctl0_byte_off + (unsigned)i * 4u);
		map_dev[wi] = events[i];
	}
}

void intel_uncore_mmio_bank_collect(struct stats_type *type,
				    const char *stats_dev_key,
				    uint32_t pci_cfg_base,
				    uint32_t *map_dev,
				    unsigned ctrctl0_byte_off,
				    const unsigned ctr_lo_byte_off[4],
				    const unsigned ctr_hi_byte_off[4])
{
	static const char *ctl_names[] = {"CTL0", "CTL1", "CTL2", "CTL3"};
	static const char *ctr_names[] = {"CTR0", "CTR1", "CTR2", "CTR3"};
	struct stats *stats = get_current_stats(type, stats_dev_key);
	int i;

	if (stats == NULL)
		return;

	for (i = 0; i < 4; i++) {
		unsigned wi = intel_uncore_mmio_word_index(
		    pci_cfg_base, ctrctl0_byte_off + (unsigned)i * 4u);
		stats_set(stats, ctl_names[i], (uint64_t)map_dev[wi]);
	}

	for (i = 0; i < 4; i++) {
		unsigned lo =
		    intel_uncore_mmio_word_index(pci_cfg_base, ctr_lo_byte_off[i]);
		unsigned hi =
		    intel_uncore_mmio_word_index(pci_cfg_base, ctr_hi_byte_off[i]);
		uint64_t v =
		    ((uint64_t)map_dev[hi] << 32) | (uint64_t)map_dev[lo];
		stats_set(stats, ctr_names[i], v);
	}
}
