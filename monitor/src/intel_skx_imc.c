/*! 
 \file intel_skx_imc.c
 \author Todd Evans 
 \brief Performance Monitoring Counters for Intel Knights Landing DRAM IMC
*/

#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include "cpuid.h"
#include "stats.h"
#include "string1.h"
#include "trace.h"
#include "msr_io.h"
#include "intel_mmconfig.h"

#define pci_cfg_address(bus, dev, func) (bus << 20) | (dev << 15) | (func << 12)
#define index(address, off) (address | off)/4

#define DCLK_PMON_UNIT_CTL_REG    0xF4
#define DCLK_PMON_UNIT_STATUS_REG 0xF8

#define DCLK_PMON_CTR0_LOW_REG  0xA0
#define DCLK_PMON_CTR0_HIGH_REG 0xA4
#define DCLK_PMON_CTR1_LOW_REG  0xA8
#define DCLK_PMON_CTR1_HIGH_REG 0xAC
#define DCLK_PMON_CTR2_LOW_REG  0xB0
#define DCLK_PMON_CTR2_HIGH_REG 0xB4
#define DCLK_PMON_CTR3_LOW_REG  0xB8
#define DCLK_PMON_CTR3_HIGH_REG 0xBC

#define DCLK_PMON_CTRCTL0_REG   0xD8
#define DCLK_PMON_CTRCTL1_REG   0xDC
#define DCLK_PMON_CTRCTL2_REG   0xE0
#define DCLK_PMON_CTRCTL3_REG   0xE4
#define U_MSR_PMON_GLOBAL_CTL   0x0700

#define CTL_KEYS					\
  X(CTL0, "C", ""),					\
    X(CTL1, "C", ""),					\
    X(CTL2, "C", ""),					\
    X(CTL3, "C", "")
#define CTR_KEYS							\
  X(CTR0, "E,W=48", ""),						\
    X(CTR1, "E,W=48", ""),						\
    X(CTR2, "E,W=48", ""),						\
    X(CTR3, "E,W=48", "")

#define KEYS CTL_KEYS CTR_KEYS

#define PERF_EVENT(event, umask)                \
  ( (event)                                     \
    | (umask << 8)                              \
    | (0UL << 17) /* Clear counter */           \
    | (0UL << 18) /* Edge Detection. */		\
    | (0UL << 20) /* Overflow disable */	\
    | (1UL << 22) /* Enable. */			\
    | (0UL << 23) /* Invert */			\
    | (0x0UL << 24) /* Threshold */		\
    )

#define CAS_READS      PERF_EVENT(0x04, 0x03)
#define CAS_WRITES     PERF_EVENT(0x04, 0x0C)
#define ACT_COUNT      PERF_EVENT(0x01, 0x0B)
#define PRE_COUNT_ALL  PERF_EVENT(0x02, 0x03)
#define PRE_COUNT_MISS PERF_EVENT(0x02, 0x01)

static int intel_skx_imc_begin_dev(uint32_t bus, uint32_t dev, uint32_t fun, uint32_t *map_dev, uint32_t *events, int nr_events)
{
  int i;
  uint32_t ctl  = 0x0UL;
  int msr_fd = -1;
  uint64_t global_ctr_ctrl;
  uint32_t pci = pci_cfg_address(bus, dev, fun);

  msr_fd = msr_open_cpu("0", O_RDWR);
  if (msr_fd < 0)
    goto out;

  /* Enable uncore counters globally. */
  global_ctr_ctrl = 1ULL << 61;
  if (msr_write_u64(msr_fd, U_MSR_PMON_GLOBAL_CTL, global_ctr_ctrl) < 0) {
    ERROR("cannot enable uncore performance counters: %m\n");
    goto out;
  }

  map_dev[index(pci, DCLK_PMON_UNIT_CTL_REG)] = ctl;
  map_dev[index(pci, DCLK_PMON_UNIT_STATUS_REG)] = ctl;

  for (i=0; i < nr_events; i++)
    map_dev[index(pci, (DCLK_PMON_CTRCTL0_REG + 4*i))] = events[i];

 out:
  if (msr_fd >= 0)
    close(msr_fd);

  return 0;
}

static void intel_skx_imc_collect_dev(struct stats_type *type, uint32_t bus, uint32_t dev, uint32_t fun,   uint32_t *map_dev)
{
  struct stats *stats = NULL;
  char dev_str[80];
  snprintf(dev_str, sizeof(dev_str), "%02x/%02x.%x", bus, dev, fun);
  stats = get_current_stats(type, dev_str);
  if (stats == NULL)
    return;

  TRACE("dev %s\n", dev_str);

  uint32_t pci = pci_cfg_address(bus, dev, fun);

#define X(k,r...)							\
  ({                                                                    \
    uint32_t val = 0;                                                   \
    val = map_dev[index(pci, DCLK_PMON_CTR##k##_REG)];			\
    stats_set(stats, #k, val);						\
  })
  CTL_KEYS;
#undef X

#define X(k,r...)							\
  ({                                                                    \
    uint64_t val = 0;                                                   \
    val = (uint64_t) (map_dev[index(pci, DCLK_PMON_##k##_HIGH_REG)]) << 32 | (uint64_t) (map_dev[index(pci, DCLK_PMON_##k##_LOW_REG)]); \
    stats_set(stats, #k, val);						\
  })
  CTR_KEYS;
#undef X
}

static uint32_t events[] = {
  CAS_READS, CAS_WRITES, ACT_COUNT, PRE_COUNT_MISS,
};
static uint32_t imc_dclk_dids[] = {0x2042, 0x2046, 0x204a};
static const uint64_t mmconfig_base = 0x80000000;
static const uint64_t mmconfig_size = 0x10000000;

static int intel_skx_imc_begin(struct stats_type *type)
{
  int nr = 0;
  char **dev_paths = NULL;
  int nr_devs = 0;
  struct intel_mmconfig mm = { -1, MAP_FAILED, 0, 0 };

  if (processor != SKYLAKE) goto out;
  if (intel_mmconfig_open(&mm, mmconfig_base, mmconfig_size) < 0)
    goto out;

  int nr_events = 4;

  if (pci_map_create(&dev_paths, &nr_devs, imc_dclk_dids, 3) < 0) {
    TRACE("Failed to identify pci devices");
    goto out;
  }
  // MC: DCLK
  // Devices: 0x10 0x10 0x11 (Controllers)
  // Functions: 0x02 0x06 0x02 (Channels)
  int i;
  for (i = 0; i < nr_devs; i++) {
    char *cursor = dev_paths[i];
    uint32_t bus = strtol(strsep_ne(&cursor, "/"), NULL, 16);
    uint32_t dev = strtol(strsep_ne(&cursor, "."), NULL, 16);
    uint32_t fun = strtol(cursor, NULL, 16);

    if (intel_skx_imc_begin_dev(bus, dev, fun, mm.map, events, nr_events) == 0)
      nr++;
  }

 out:
  if (dev_paths != NULL)
    pci_map_destroy(&dev_paths, nr_devs);
  intel_mmconfig_close(&mm);

  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

static void intel_skx_imc_collect(struct stats_type *type)
{
  char **dev_paths = NULL;
  int nr_devs = 0;
  struct intel_mmconfig mm = { -1, MAP_FAILED, 0, 0 };

  if (intel_mmconfig_open(&mm, mmconfig_base, mmconfig_size) < 0)
    goto out;

  if (pci_map_create(&dev_paths, &nr_devs, imc_dclk_dids, 3) < 0) {
    TRACE("Failed to identify pci devices");
    goto out;
  }

  int i;
  for (i = 0; i < nr_devs; i++) {
      char *cursor = dev_paths[i];
      uint32_t bus = strtol(strsep_ne(&cursor, "/"), NULL, 16);
      uint32_t dev = strtol(strsep_ne(&cursor, "."), NULL, 16);
      uint32_t fun = strtol(cursor, NULL, 16);

      intel_skx_imc_collect_dev(type, bus, dev, fun, mm.map);
  }
 out:
  if (dev_paths != NULL)
    pci_map_destroy(&dev_paths, nr_devs);
  intel_mmconfig_close(&mm);
}

struct stats_type intel_skx_imc_stats_type = {
  .st_name = "intel_skx_imc",
  .st_begin = &intel_skx_imc_begin,
  .st_collect = &intel_skx_imc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
