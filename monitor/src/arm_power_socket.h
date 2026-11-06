#ifndef ARM_POWER_SOCKET_H_
#define ARM_POWER_SOCKET_H_

/*
 * host_power_socket: one row per CPU socket (dev = socket id) with the ACPI power meters of
 * that socket (round 2, D31/D32). Node-wide data: on shared nodes it travels only in the `-`
 * payload (job_emit_filter.c sends every type that is not proc/GPU/CPU/host_mem_job there, D38).
 * Each key is 0 when its meter is absent; a node with no meters disables the type.
 */

#include "arm_power_hwmon.h"

#define ARM_POWER_SOCKET_ST_NAME "host_power_socket"

#define ARM_POWER_SOCKET_KEYS                                                                      \
  X(cpu_power_w, "U=W", "CPU core rail power of this socket (ACPI CPU Power Socket N)"),           \
      X(socket_power_w, "U=W", "Whole-socket power (ACPI Grace Power Socket N)"),                  \
      X(sysio_power_w, "U=W", "SoC / IO rail power of this socket (ACPI SysIO Power Socket N)"),   \
      X(module_power_w, "U=W", "Whole module power, GH200 only (ACPI Module Power Socket N)")

struct stats;

/* Key names in enum arm_power_kind order. */
extern const char *const arm_power_socket_keys[ARM_POWER_KIND_COUNT];

/* Publish every key for one socket: slots[kind] is an arm_power_hwmon slot or -1 (publishes 0). */
void arm_power_socket_publish(struct stats *stats, struct arm_power_hwmon *h,
                              const int slots[ARM_POWER_KIND_COUNT]);

#endif
