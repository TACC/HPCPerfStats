#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"

#define CTR_KEYS                                                             \
  X(REQUESTS_READS, "E,W=48", ""),                                             \
      X(REQUESTS_WRITES, "E,W=48", ""),                                       \
      X(CLOCKTICKS, "E,W=48", ""),                                            \
      X(IMC_WRITES, "E,W=48", "")

#define KEYS CTR_KEYS

#define PERF_EVENT(event, umask)                                             \
  ((event) | (umask << 8) | (0UL << 18) | (1UL << 22) | (0UL << 23)           \
   | (0x01UL << 24))

#define REQUESTS_READS	 PERF_EVENT(0x01, 0x03)
#define REQUESTS_WRITES PERF_EVENT(0x01, 0x0C)
#define CLOCKTICKS	 PERF_EVENT(0x00, 0x00)
#define IMC_WRITES	 PERF_EVENT(0x1A, 0x0F)

static uint32_t events[] = {
    REQUESTS_READS,
    REQUESTS_WRITES,
    CLOCKTICKS,
    IMC_WRITES,
};
static const char *const event_keys[] = {
    "REQUESTS_READS",
    "REQUESTS_WRITES",
    "CLOCKTICKS",
    "IMC_WRITES",
};
static int dids[] = {0x3c46};

static const struct intel_uncore_pci_cfg intel_snb_hau_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 1,
    .events = events,
    .event_keys = event_keys,
    .fixed_ctr_key = NULL,
    .nr_events = 4,
};

static int intel_snb_hau_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_snb_hau_pci_cfg, type);
}

static void intel_snb_hau_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_snb_hau_pci_cfg, type);
}

struct stats_type intel_snb_hau_stats_type = {
    .st_name = "intel_snb_hau",
    .st_begin = &intel_snb_hau_begin,
    .st_collect = &intel_snb_hau_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
