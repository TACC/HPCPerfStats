/*! \file intel_uncore_mmio.h
 *  Helpers for PCI CFG space MMIO-mapped uncore PMON banks (32-bit dword indexing).
 */

#ifndef INTEL_UNCORE_MMIO_H
#define INTEL_UNCORE_MMIO_H

#include <stdint.h>

struct stats_type;

unsigned intel_uncore_mmio_word_index(uint32_t pci_cfg_base, uint32_t byte_off);

void intel_uncore_mmio_bank_program(uint32_t *map_dev, uint32_t pci_cfg_base,
				  unsigned unit_ctl_byte_off,
				  unsigned unit_status_byte_off,
				  unsigned ctrctl0_byte_off,
				  const uint32_t *events, int nr_events);

void intel_uncore_mmio_bank_collect(struct stats_type *type,
				    const char *stats_dev_key,
				    uint32_t pci_cfg_base,
				    uint32_t *map_dev,
				    const char *const ctr_keys[4],
				    unsigned ctrctl0_byte_off,
				    const unsigned ctr_lo_byte_off[4],
				    const unsigned ctr_hi_byte_off[4]);

#endif
