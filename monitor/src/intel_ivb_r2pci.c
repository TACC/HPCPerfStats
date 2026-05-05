#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"

#define CTL_KEYS                                                             \
  X(CTL0, "C", ""),                                                          \
      X(CTL1, "C", ""),                                                      \
      X(CTL2, "C", ""),                                                      \
      X(CTL3, "C", "")

#define CTR_KEYS                                                             \
  X(CTR0, "E,W=44", ""),                                                     \
      X(CTR1, "E,W=44", ""),                                                 \
      X(CTR2, "E,W=44", ""),                                                 \
      X(CTR3, "E,W=44", "")

#define KEYS CTL_KEYS, CTR_KEYS

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
static int dids[] = {0x0e34};

static const struct intel_uncore_pci_cfg intel_ivb_r2pci_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 1,
    .events = events,
    .nr_events = 4,
};

static int intel_ivb_r2pci_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_ivb_r2pci_pci_cfg, type);
}

static void intel_ivb_r2pci_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_ivb_r2pci_pci_cfg, type);
}

struct stats_type intel_ivb_r2pci_stats_type = {
    .st_name = "intel_ivb_r2pci",
    .st_begin = &intel_ivb_r2pci_begin,
    .st_collect = &intel_ivb_r2pci_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
