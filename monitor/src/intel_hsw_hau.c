/*! \file intel_hsw_hau.c
 *  Intel Haswell HA uncore (intel_x86_uncore_hau_hsw).
 */

#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"

#define CTR_KEYS                                                             \
  X(requests_reads, "E,W=48", ""),                                             \
      X(requests_writes, "E,W=48", ""),                                       \
      X(clockticks, "E,W=48", ""),                                            \
      X(imc_writes, "E,W=48", "")

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
    "requests_reads",
    "requests_writes",
    "clockticks",
    "imc_writes",
};
static int dids[] = {0x2F30, 0x2F38};

static const struct intel_uncore_pci_cfg intel_hsw_hau_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 2,
    .events = events,
    .event_keys = event_keys,
    .fixed_ctr_key = NULL,
    .nr_events = 4,
};

static int intel_hsw_hau_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_hsw_hau_pci_cfg, type);
}

static void intel_hsw_hau_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_hsw_hau_pci_cfg, type);
}

struct stats_type intel_hsw_hau_stats_type = {
    .st_name = "intel_x86_uncore_hau_hsw",
    .st_begin = &intel_hsw_hau_begin,
    .st_collect = &intel_hsw_hau_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
