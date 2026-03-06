#include <stdio.h>
#include <errno.h>
#include <limits.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <inttypes.h>

#define NFIELDS 11

// A list of avaialble dcgm metrics 
// https://github.com/NVIDIA/gpu-monitoring-tools/tree/master/etc/dcgm-exporter
// https://github.com/NVIDIA/DCGM/blob/7e1012302679e4bb7496483b32dcffb56e528c92/dcgmlib/src/dcgm_fields.cpp

#define KEYS \
  X(gpu_util,     "",     "GPU utilization in %"), \
  X(mem_util,     "",     "Memory utilization in %"), \
  X(power_usage,  "U=W",  "Power draw in Watts"), \
  X(temperature,  "U=C",  "GPU temperature in C"), \
  X(fp64_active,  "",     "Ratio of cycles the fp64 pipes are active (in %)"), \
  X(sm_active,    "",     "The ratio of cycles an SM has at least 1 warp assigned (in %)"), \
  X(sm_occupancy, "",     "The ratio of number of warps resident on an SM (in %)"), \
  X(fp32_active,  "",     "Ratio of cycles the fp32 pipes are active (in %)"), \
  X(fp16_active,  "",     "Ratio of cycles the fp16 pipes are active (in %)"), \
  X(tensor_active,      "", "The ratio of cycles the any tensor pipe is active (off the peak sustained elapsed cycles)"), \
  X(clocks_event_reasons,      "", "Integer indicating specific closck slowdown reason"), \
  // X(fb_total,     "U=MB", "Framebuffer memory total (in MiB)"), \
  // X(fb_used,      "U=MB", "Framebuffer memory used (in MiB)"), \
  // X(fb_free,      "U=MB", "Framebuffer memory free (in MiB)"), \
  // X(pcie_tx_bytes,"U=B",  "The number of bytes of active PCIe tx (transmit) data including both header and payload"), \
  // X(pcie_rx_bytes,"U=B",  "The number of bytes of active PCIe rx (read) data including both header and payload"), \
  // X(pcie_replay_counter,"", "Total number of PCIe retries"), \
  // X(c2c_rx_bytes,      "", "C2C bytes)"), \

typedef struct DCMG_DATA {
    int64_t mem_util;
    int64_t gpu_util;
    int64_t fb_total;
    int64_t fb_used;
    int64_t fb_free;
    int64_t temperature;
    int64_t clocks_event_reasons;

    // int64_t pcie_rx_bytes;
    // int64_t pcie_tx_bytes;
    // int64_t pcie_replay_counter;

    // int64_t c2c_rx_bytes;
    // int64_t c2c_tx_bytes;
    // int64_t c2c_all_bytes;

    double power_usage;
    double tensor_active;
    double fp64_active;
    double fp32_active;
    double fp16_active;
    double sm_active;
    double sm_occupancy;
} DCMG_DATA;