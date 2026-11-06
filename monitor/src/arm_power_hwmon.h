#ifndef ARM_POWER_HWMON_H_
#define ARM_POWER_HWMON_H_

/*
 * ACPI power meters (acpi_power_meter hwmon) labelled "<Name> Power Socket <N>" on NVIDIA Grace
 * nodes (GB200, GH200, Grace/Grace). Labels come from power1_oem_info (or power1_label), and
 * the attributes live in hwmonN/ on EL10 kernels and in hwmonN/device/ on EL9. hwmon numbers are
 * not stable, so meters are indexed by label only. One fd per meter, opened once; reads are a
 * single pread of power1_average (microwatts). Replaces DCGM fields 1130/1132/1133 (round 2,
 * D9/D31): DCGM read 0 W for every Grace meter on GB200 (finding 22).
 */

#include <stddef.h>

/* Meter kinds; each maps to one host_power_socket key. */
enum arm_power_kind {
  ARM_POWER_CPU = 0, /* "CPU Power Socket N": CPU core rail  -> cpu_power_w */
  ARM_POWER_SOCKET,  /* "Grace Power Socket N": whole socket -> socket_power_w */
  ARM_POWER_SYSIO,   /* "SysIO Power Socket N": SoC rail     -> sysio_power_w */
  ARM_POWER_MODULE,  /* "Module Power Socket N": GH200 module -> module_power_w */
  ARM_POWER_KIND_COUNT
};

#define ARM_POWER_HWMON_MAX_METERS 32
#define ARM_POWER_HWMON_MAX_SOCKETS 16
#define ARM_POWER_HWMON_SYSFS_ROOT "/sys"

struct arm_power_meter {
  int kind;
  int sock;
  int fd;
  int read_warned;
};

struct arm_power_hwmon {
  struct arm_power_meter m[ARM_POWER_HWMON_MAX_METERS];
  int n;     /* known meters held open */
  int nsock; /* highest socket id seen + 1 */
};

/* Parse "<Name> Power Socket <N>". Returns 0 for a known name (kind/sock set), 1 for a
 * well-formed label with an unknown name (sock set, e.g. a future Vera meter), -1 otherwise. */
int arm_power_hwmon_parse_label(const char *label, int *kind, int *sock);
/* Scan <sysfs_root>/class/hwmon/hwmon* once and open every known meter. Unknown socket-meter
 * labels are logged once and ignored. Returns the number of meters opened (0 = none). */
int arm_power_hwmon_open(struct arm_power_hwmon *h, const char *sysfs_root);
/* Meter slot for (kind, sock), or -1. */
int arm_power_hwmon_find(const struct arm_power_hwmon *h, int kind, int sock);
/* Watts from one pread of power1_average; 0 (and a one-shot warn) when unreadable. */
double arm_power_hwmon_read_w(struct arm_power_hwmon *h, int slot);
void arm_power_hwmon_close(struct arm_power_hwmon *h);

#endif
