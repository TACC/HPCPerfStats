#include "stats.h"
#include "JOIN.h"
#include "intel_uncore_pci.h"
#include "intel_pmc_uncore.h"

#define CTR_KEYS                                                             \
  X(dram_cas_reads, "E,W=48", ""),                                                \
      X(dram_cas_writes, "E,W=48", ""),                                           \
      X(dram_act_count, "E,W=48", ""),                                            \
      X(dram_pre_count_miss, "E,W=48", ""),                                       \
      X(dram_fixed_ctr, "E,W=48", "")

#define KEYS CTR_KEYS

#define MBOX_PERF_EVENT(event, umask)                                        \
  ((event) | (umask << 8) | (0UL << 18) | (1UL << 22) | (0UL << 23)          \
   | (0x01UL << 24))

#define CAS_READS	    MBOX_PERF_EVENT(0x04, 0x03)
#define CAS_WRITES	    MBOX_PERF_EVENT(0x04, 0x0C)
#define ACT_COUNT	    MBOX_PERF_EVENT(0x01, 0x0B)
#define PRE_COUNT_MISS	    MBOX_PERF_EVENT(0x02, 0x01)

static uint32_t events[] = {
    CAS_READS,
    CAS_WRITES,
    ACT_COUNT,
    PRE_COUNT_MISS,
};
static const char *const event_keys[] = {
    "dram_cas_reads",
    "dram_cas_writes",
    "dram_act_count",
    "dram_pre_count_miss",
};
static int dids[] = {0x2fb0, 0x2fb1, 0x2fb4, 0x2fb5,
		     0x2fd0, 0x2fd1, 0x2fd4, 0x2fd5};

static const struct intel_uncore_pci_cfg intel_hsw_imc_pci_cfg = {
    .pci_dids = dids,
    .nr_pci_dids = 8,
    .events = events,
    .event_keys = event_keys,
    .fixed_ctr_key = "dram_fixed_ctr",
    .nr_events = 4,
};

static int intel_hsw_imc_begin(struct stats_type *type)
{
  return intel_uncore_pci_begin(&intel_hsw_imc_pci_cfg, type);
}

static void intel_hsw_imc_collect(struct stats_type *type)
{
  intel_uncore_pci_collect(&intel_hsw_imc_pci_cfg, type);
}

struct stats_type intel_hsw_imc_stats_type = {
    .st_name = "intel_x86_uncore_imc_hsw",
    .st_begin = &intel_hsw_imc_begin,
    .st_collect = &intel_hsw_imc_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
