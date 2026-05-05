#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"

#define CTL_KEYS                                                             \
  X(CTL0, "C", ""),                                                          \
      X(CTL1, "C", ""),                                                      \
      X(CTL2, "C", ""),                                                      \
      X(CTL3, "C", "")

#define CTR_KEYS                                                             \
  X(CTR0, "E,W=48,U=flt", ""),                                               \
      X(CTR1, "E,W=48,U=flt", ""),                                           \
      X(CTR2, "E,W=48,U=flt", ""),                                           \
      X(CTR3, "E,W=48,U=flt", "")

#define KEYS CTL_KEYS, CTR_KEYS

#define PERF_EVENT(event, umask)                                             \
  ((event) | (umask << 8) | (0UL << 18) | (1UL << 21) | (1UL << 22)           \
   | (0UL << 23))

#define TxL_FLITS_G1_SNP PERF_EVENT(0x00, 0x01)
#define TxL_FLITS_G1_HOM PERF_EVENT(0x00, 0x04)
#define G1_DRS_DATA	 PERF_EVENT(0x02, 0x08)
#define G2_NCB_DATA	 PERF_EVENT(0x03, 0x04)

static int dids[] = {0x2F32, 0x2F33, 0x2F3A};
static uint32_t events[] = {
    TxL_FLITS_G1_SNP,
    TxL_FLITS_G1_HOM,
    G1_DRS_DATA,
    G2_NCB_DATA,
};

static const struct intel_uncore_pci_cfg intel_hsw_qpi_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 3,
    .events = events,
    .nr_events = 4,
};

static int intel_hsw_qpi_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_hsw_qpi_pci_cfg, type);
}

static void intel_hsw_qpi_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_hsw_qpi_pci_cfg, type);
}

struct stats_type intel_hsw_qpi_stats_type = {
    .st_name = "intel_hsw_qpi",
    .st_begin = &intel_hsw_qpi_begin,
    .st_collect = &intel_hsw_qpi_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
