/*! \file intel_uncore_pci.h
 *  PCI uncore configuration for intel_x86_uncore_* types.
 */

#ifndef INTEL_UNCORE_PCI_H_
#define INTEL_UNCORE_PCI_H_

#include <stdint.h>

#include "stats.h"

/*
 * SNB–BDW-style uncore boxes enumerated via PCI DID lists + fixed event
 * programming passed to intel_pmc_uncore_*.
 */
struct intel_uncore_pci_cfg {
  const int *pci_dids;
  int nr_pci_dids;
  const uint32_t *events;
  const char *const *event_keys;
  const char *fixed_ctr_key;
  int nr_events;
};

int intel_uncore_pci_begin(const struct intel_uncore_pci_cfg *cfg, struct stats_type *type);

void intel_uncore_pci_collect(const struct intel_uncore_pci_cfg *cfg, struct stats_type *type);

#endif
