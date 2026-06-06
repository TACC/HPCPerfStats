#include "stats.h"
#include "JOIN.h"
#include "likwid_uncore_adapter.h"

#define KEYS \
  X(tx_r_inserts, "E,W=44", ""), \
  X(ring_bl_used_all, "E,W=44", ""), \
  X(ring_ad_used_all, "E,W=44", ""), \
  X(ring_ak_used_all, "E,W=44", "")

static int intel_snb_r2pci_begin(struct stats_type *type)
{
  return likwid_uncore_adapter_begin(type, LIKWID_UNCORE_PROFILE_R2PCI_SNB);
}

static void intel_snb_r2pci_collect(struct stats_type *type)
{
  likwid_uncore_adapter_collect(type, LIKWID_UNCORE_PROFILE_R2PCI_SNB);
}

struct stats_type intel_snb_r2pci_stats_type = {
  .st_name = "intel_x86_uncore_r2pci_snb",
  .st_begin = &intel_snb_r2pci_begin,
  .st_collect = &intel_snb_r2pci_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
