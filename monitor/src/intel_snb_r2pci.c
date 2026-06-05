/*! \file intel_snb_r2pci.c
 *  Intel Sandy Bridge R2PCI uncore (intel_x86_uncore_r2pci_snb).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"

#define CTR_KEYS                                                             \
  X(tx_r_inserts, "E,W=44", ""),                                                \
      X(ring_bl_used_all, "E,W=44", ""),                                      \
      X(ring_ad_used_all, "E,W=44", ""),                                      \
      X(ring_ak_used_all, "E,W=44", "")

#define KEYS CTR_KEYS

#define PERF_EVENT(event, umask)                                             \
  ((event) | (umask << 8) | (0UL << 18) | (1UL << 22) | (0UL << 23)           \
   | (0x01UL << 24))

#define TxR_INSERTS	 PERF_EVENT(0x24, 0x04)
#define RING_AD_USED_ALL PERF_EVENT(0x07, 0x0F)
#define RING_AK_USED_ALL PERF_EVENT(0x08, 0x0F)
#define RING_BL_USED_ALL PERF_EVENT(0x09, 0x0F)

static uint32_t events[] = {
    TxR_INSERTS,
    RING_BL_USED_ALL,
    RING_AD_USED_ALL,
    RING_AK_USED_ALL,
};
static const char *const event_keys[] = {
    "tx_r_inserts",
    "ring_bl_used_all",
    "ring_ad_used_all",
    "ring_ak_used_all",
};
static int dids[] = {0x3c43};

static const struct intel_uncore_pci_cfg intel_snb_r2pci_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 1,
    .events = events,
    .event_keys = event_keys,
    .fixed_ctr_key = NULL,
    .nr_events = 4,
};

static int intel_snb_r2pci_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_snb_r2pci_pci_cfg, type);
}

static void intel_snb_r2pci_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_snb_r2pci_pci_cfg, type);
}

struct stats_type intel_snb_r2pci_stats_type = {
    .st_name = "intel_x86_uncore_r2pci_snb",
    .st_begin = &intel_snb_r2pci_begin,
    .st_collect = &intel_snb_r2pci_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
