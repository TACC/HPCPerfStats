/*! 
 \file intel_knl_mc.c
 \author Todd Evans 
 \brief Performance Monitoring Counters for Intel Knights Landing DRAM MC
*/

#include <stddef.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include "cpuid.h"
#include "stats.h"
#include "trace.h"
#include "path_open_fail_once.h"

#define pci_cfg_address(bus, dev, func) (bus << 20) | (dev << 15) | (func << 12)
#define reg(address, off) (address | off)/4

#define UCLK_PMON_UNIT_CTL_REG    0x430
#define UCLK_PMON_UNIT_STATUS_REG 0x434

#define UCLK_PMON_CTR0_LOW_REG  0x400
#define UCLK_PMON_CTR0_HIGH_REG 0x404
#define UCLK_PMON_CTR1_LOW_REG  0x408
#define UCLK_PMON_CTR1_HIGH_REG 0x40C
#define UCLK_PMON_CTR2_LOW_REG  0x410
#define UCLK_PMON_CTR2_HIGH_REG 0x414
#define UCLK_PMON_CTR3_LOW_REG  0x418
#define UCLK_PMON_CTR3_HIGH_REG 0x41C

#define UCLK_PMON_CTRCTL0_REG   0x420
#define UCLK_PMON_CTRCTL1_REG   0x424
#define UCLK_PMON_CTRCTL2_REG   0x428
#define UCLK_PMON_CTRCTL3_REG   0x42C

#define DCLK_PMON_UNIT_CTL_REG    0xB30
#define DCLK_PMON_UNIT_STATUS_REG 0xB34

#define DCLK_PMON_CTR0_LOW_REG  0xB00
#define DCLK_PMON_CTR0_HIGH_REG 0xB04
#define DCLK_PMON_CTR1_LOW_REG  0xB08
#define DCLK_PMON_CTR1_HIGH_REG 0xB0C
#define DCLK_PMON_CTR2_LOW_REG  0xB10
#define DCLK_PMON_CTR2_HIGH_REG 0xB14
#define DCLK_PMON_CTR3_LOW_REG  0xB18
#define DCLK_PMON_CTR3_HIGH_REG 0xB1C

#define DCLK_PMON_CTRCTL0_REG   0xB20
#define DCLK_PMON_CTRCTL1_REG   0xB24
#define DCLK_PMON_CTRCTL2_REG   0xB28
#define DCLK_PMON_CTRCTL3_REG   0xB2C

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

#define UCLK_CYCLES    PERF_EVENT(0x00, 0x00)
#define CAS_READS      PERF_EVENT(0x03, 0x01)
#define CAS_WRITES     PERF_EVENT(0x03, 0x02)
#define DCLK_CYCLES    PERF_EVENT(0x00, 0x00)

#define BUS 0xFF

static int intel_knl_mc_uclk_begin_dev(uint32_t dev, uint32_t *map_dev, uint32_t *events, int nr_events)
{
  int i;
  uint32_t ctl  = 0x0UL;
  size_t n = 4;

  uint32_t pci = pci_cfg_address(BUS, dev, 0x00);

  memcpy(&map_dev[reg(pci, UCLK_PMON_UNIT_CTL_REG)], &ctl, n);
  memcpy(&map_dev[reg(pci, UCLK_PMON_UNIT_STATUS_REG)], &ctl, n);

  for (i=0; i < nr_events; i++) {
    memcpy(&map_dev[reg(pci, UCLK_PMON_CTRCTL0_REG +4*i)], &events[i], n);
  }
  return 0;
}

static int intel_knl_mc_dclk_begin_dev(uint32_t dev, uint32_t func, uint32_t *map_dev, uint32_t *events, int nr_events)
{
  int i;
  uint32_t ctl  = 0x0UL;
  size_t n = 4;

  uint32_t pci = pci_cfg_address(BUS, dev, func);
  memcpy(&map_dev[reg(pci, DCLK_PMON_UNIT_CTL_REG)], &ctl, n);
  memcpy(&map_dev[reg(pci, DCLK_PMON_UNIT_STATUS_REG)], &ctl, n);

  for (i=0; i < nr_events; i++) {
    memcpy(&map_dev[reg(pci, DCLK_PMON_CTRCTL0_REG +4*i)], &events[i], n);
  }
  return 0;
}


static int intel_knl_mc_uclk_collect_dev(struct stats_type *type, uint32_t dev, uint32_t *map_dev)
{
  struct stats *stats = NULL;
  char dev_str[80];
  snprintf(dev_str, sizeof(dev_str), "%02x/%02x.0", BUS, dev);
  stats = get_current_stats(type, dev_str);

  if (stats == NULL)
    return;

  TRACE("dev %s\n", dev_str);

  uint32_t pci = pci_cfg_address(BUS, dev, 0x00);
#define X(k,r...)							\
  ({                                                                    \
    uint32_t val = 0;                                                   \
    val = map_dev[reg(pci, UCLK_PMON_CTR##k##_REG)];			\
    stats_set(stats, #k, val);						\
  })
  CTL_KEYS;
#undef X
#define X(k,r...)							\
  ({                                                                    \
    uint64_t val = 0;                                                   \
    val = (uint64_t) (map_dev[reg(pci, UCLK_PMON_##k##_HIGH_REG)]) << 32 | (uint64_t) (map_dev[reg(pci, UCLK_PMON_##k##_LOW_REG)]); \
    stats_set(stats, #k, val);						\
  })
  CTR_KEYS;
#undef X
}

static void intel_knl_mc_dclk_collect_dev(struct stats_type *type, uint32_t func, uint32_t dev,   uint32_t *map_dev)
{
  struct stats *stats = NULL;
  char dev_str[80];
  snprintf(dev_str, sizeof(dev_str), "%02x/%02x.%x", BUS, dev, func);
  stats = get_current_stats(type, dev_str);
  if (stats == NULL)
    return;

  TRACE("dev %s\n", dev_str);

  uint32_t pci = pci_cfg_address(BUS, dev, func);
#define X(k,r...)							\
  ({                                                                    \
    uint32_t val = 0;                                                   \
    val = map_dev[reg(pci, DCLK_PMON_CTR##k##_REG)];			\
    stats_set(stats, #k, val);						\
  })
  CTL_KEYS;
#undef X

#define X(k,r...)							\
  ({                                                                    \
    uint64_t val = 0;                                                   \
    val = (uint64_t) (map_dev[reg(pci, DCLK_PMON_##k##_HIGH_REG)]) << 32 | (uint64_t) (map_dev[reg(pci, DCLK_PMON_##k##_LOW_REG)]); \
    stats_set(stats, #k, val);						\
  })
  CTR_KEYS;
#undef X
}

int nr_mc_devs = 2;
// MC: UCLK
// Devices: 0x0a 0x0b (Controllers)
// Functions: 0x00
uint32_t mc_uclk_events[] = { UCLK_CYCLES };
int nr_mc_uclk_events = 1;
uint32_t mc_uclk_dev[] = {0x0a, 0x0b};
// MC: DCLK
// Devices: 0x08 0x09 (Controllers)
// Functions: 0x02 0x03 0x04 (Channels)
uint32_t mc_dclk_events[] = { CAS_READS, CAS_WRITES, DCLK_CYCLES };
int nr_mc_dclk_events = 3;
uint32_t mc_dclk_dev[] = {0x08, 0x09};

static int intel_knl_mc_begin(struct stats_type *type)
{
  int nr = 0;
  int n_pmcs = 0;
  int fd = -1;

  if (processor != KNL) goto out;
  const char *path = "/dev/mem";
  uint64_t mmconfig_base = 0xc0000000;
  uint64_t mmconfig_size = 0x10000000;
  uint32_t *mmconfig_ptr;

  if (path_open_is_skipped(path))
    goto out;
  fd = open(path, O_RDWR);    // first check to see if file can be opened with read permission
  if (fd < 0) {
    path_open_record_failure_once(path);
    goto out;
  }
  mmconfig_ptr = mmap(NULL, mmconfig_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, mmconfig_base);
  if (mmconfig_ptr == MAP_FAILED) {
    ERROR("cannot mmap `%s': %m\n", path);
    goto out;
  }

  int i;  
  for (i = 0; i < nr_mc_devs; i++) {
    if (intel_knl_mc_uclk_begin_dev(mc_uclk_dev[i], mmconfig_ptr, mc_uclk_events, nr_mc_uclk_events) == 0)
      nr++;      
    if (intel_knl_mc_dclk_begin_dev(mc_dclk_dev[i], 0x02, mmconfig_ptr, mc_dclk_events, nr_mc_dclk_events) == 0)
      nr++;      
    if (intel_knl_mc_dclk_begin_dev(mc_dclk_dev[i], 0x03, mmconfig_ptr, mc_dclk_events, nr_mc_dclk_events) == 0)
      nr++;      
    if (intel_knl_mc_dclk_begin_dev(mc_dclk_dev[i], 0x04, mmconfig_ptr, mc_dclk_events, nr_mc_dclk_events) == 0)
      nr++;
  }
  munmap(mmconfig_ptr, mmconfig_size);

 out:
  if (fd >= 0)
    close(fd);
  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;  
}

static void intel_knl_mc_collect(struct stats_type *type)
{
  const char *path = "/dev/mem";
  uint64_t mmconfig_base = 0xc0000000;
  uint64_t mmconfig_size = 0x10000000;
  uint32_t *mmconfig_ptr;
  int fd = -1;

  if (path_open_is_skipped(path))
    goto out;
  fd = open(path, O_RDWR);    // first check to see if file can be opened with read permission
  if (fd < 0) {
    path_open_record_failure_once(path);
    goto out;
  }
  mmconfig_ptr = mmap(NULL, mmconfig_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, mmconfig_base);
  if (mmconfig_ptr == MAP_FAILED) {
    ERROR("cannot mmap `%s': %m\n", path);
    goto out;
  }

  int i;
  int nr_mc_devs = 2;

  uint32_t mc_uclk_dev[] = {0x0a, 0x0b};
  uint32_t mc_dclk_dev[] = {0x08, 0x09};
  for (i = 0; i < nr_mc_devs; i++) {
    intel_knl_mc_uclk_collect_dev(type, mc_uclk_dev[i], mmconfig_ptr);
    intel_knl_mc_dclk_collect_dev(type, 0x02, mc_dclk_dev[i], mmconfig_ptr);
    intel_knl_mc_dclk_collect_dev(type, 0x03, mc_dclk_dev[i], mmconfig_ptr);
    intel_knl_mc_dclk_collect_dev(type, 0x04, mc_dclk_dev[i], mmconfig_ptr);
  }  
  munmap(mmconfig_ptr, mmconfig_size);

 out:
  if (fd >= 0)
    close(fd);
}

struct stats_type intel_knl_mc_stats_type = {
  .st_name = "intel_knl_mc",
  .st_begin = &intel_knl_mc_begin,
  .st_collect = &intel_knl_mc_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
