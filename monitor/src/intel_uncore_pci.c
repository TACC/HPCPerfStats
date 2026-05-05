#include "intel_uncore_pci.h"

#include <stdint.h>
#include <stdlib.h>

#include "intel_pmc_uncore.h"
#include "pci.h"
#include "trace.h"

int intel_uncore_pci_begin(const struct intel_uncore_pci_cfg *cfg,
			   struct stats_type *type)
{
  int nr = 0;
  char **dev_paths = NULL;
  int nr_devs;
  int i;

  if (pci_map_create(&dev_paths, &nr_devs,
		     (int *)(uintptr_t)cfg->pci_dids, cfg->nr_pci_dids) < 0)
    TRACE("Failed to identify pci devices");

  for (i = 0; i < nr_devs; i++)
    if (intel_pmc_uncore_begin_dev(dev_paths[i],
				   (uint32_t *)(uintptr_t)cfg->events,
				   cfg->nr_events) == 0)
      nr++;

  if (nr == 0)
    type->st_enabled = 0;

  pci_map_destroy(&dev_paths, nr_devs);
  return nr > 0 ? 0 : -1;
}

void intel_uncore_pci_collect(const struct intel_uncore_pci_cfg *cfg,
			      struct stats_type *type)
{
  char **dev_paths = NULL;
  int nr_devs;
  int i;

  if (pci_map_create(&dev_paths, &nr_devs,
		     (int *)(uintptr_t)cfg->pci_dids, cfg->nr_pci_dids) < 0)
    TRACE("Failed to identify pci devices");

  for (i = 0; i < nr_devs; i++)
    intel_pmc_uncore_collect_dev(type, dev_paths[i]);

  pci_map_destroy(&dev_paths, nr_devs);
}
