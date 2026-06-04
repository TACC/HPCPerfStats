#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"
#include "intel_pmc_uncore.h"

#define CTR_KEYS                                                             \
  X(CAS_READS, "E,W=48", ""),                                                \
      X(CAS_WRITES, "E,W=48", ""),                                           \
      X(ACT_COUNT, "E,W=48", ""),                                            \
      X(PRE_COUNT_MISS, "E,W=48", ""),                                       \
      X(FIXED_CTR, "E,W=48", "")

#define KEYS CTR_KEYS

#define PERF_EVENT(event, umask)                                             \
  ((event) | (umask << 8) | (0UL << 18) | (1UL << 22) | (0UL << 23)           \
   | (0x01UL << 24))

#define CAS_READS	    PERF_EVENT(0x04, 0x03)
#define CAS_WRITES	    PERF_EVENT(0x04, 0x0C)
#define ACT_COUNT	    PERF_EVENT(0x01, 0x00)
#define PRE_COUNT_MISS	    PERF_EVENT(0x02, 0x01)

static uint32_t events[] = {
    CAS_READS,
    CAS_WRITES,
    ACT_COUNT,
    PRE_COUNT_MISS,
};
static const char *const event_keys[] = {
    "CAS_READS",
    "CAS_WRITES",
    "ACT_COUNT",
    "PRE_COUNT_MISS",
};
static int dids[] = {0x3cb0, 0x3cb1, 0x3cb4, 0x3cb5};

static const struct intel_uncore_pci_cfg intel_snb_imc_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 4,
    .events = events,
    .event_keys = event_keys,
    .fixed_ctr_key = "FIXED_CTR",
    .nr_events = 4,
};

static int intel_snb_imc_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_snb_imc_pci_cfg, type);
}

static void intel_snb_imc_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_snb_imc_pci_cfg, type);
}

struct stats_type intel_snb_imc_stats_type = {
    .st_name = "intel_snb_imc",
    .st_begin = &intel_snb_imc_begin,
    .st_collect = &intel_snb_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
