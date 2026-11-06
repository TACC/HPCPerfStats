/* host_power_socket — per-socket ACPI power meters (Grace: CPU, Grace/socket, SysIO, Module).
 * Begin scans hwmon once and keeps one fd per meter; collect is one pread per meter. */
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "monitor_log.h"
#include "arm_power_socket.h"

const char *const arm_power_socket_keys[ARM_POWER_KIND_COUNT] = {"cpu_power_w", "socket_power_w",
                                                                 "sysio_power_w", "module_power_w"};

static struct arm_power_hwmon g_arm_power;
static int g_arm_power_slots[ARM_POWER_HWMON_MAX_SOCKETS][ARM_POWER_KIND_COUNT];

static unsigned long long arm_power_watts_to_ull(double w)
{
  return (w > 0.0) ? (unsigned long long)(w + 0.5) : 0ULL;
}

void arm_power_socket_publish(struct stats *stats, struct arm_power_hwmon *h,
                              const int slots[ARM_POWER_KIND_COUNT])
{
  int k;

  if (stats == NULL || slots == NULL)
    return;
  for (k = 0; k < ARM_POWER_KIND_COUNT; k++)
    stats_set(stats, arm_power_socket_keys[k],
              arm_power_watts_to_ull(arm_power_hwmon_read_w(h, slots[k])));
}

static int arm_power_socket_begin(struct stats_type *type)
{
  int s, k;

  arm_power_hwmon_close(&g_arm_power);
  if (arm_power_hwmon_open(&g_arm_power, ARM_POWER_HWMON_SYSFS_ROOT) <= 0) {
    /* Every x86 node lands here; the startup summary already lists the type as disabled. */
    monitor_log_debug("host_power_socket: no ACPI '<Name> Power Socket <N>' meters; disabled\n");
    type->st_enabled = 0;
    return 0;
  }
  for (s = 0; s < g_arm_power.nsock; s++) {
    for (k = 0; k < ARM_POWER_KIND_COUNT; k++)
      g_arm_power_slots[s][k] = arm_power_hwmon_find(&g_arm_power, k, s);
  }
  monitor_log_info("host_power_socket: %d meters on %d sockets\n", g_arm_power.n,
                   g_arm_power.nsock);
  return 0;
}

static void arm_power_socket_collect(struct stats_type *type)
{
  int s;

  for (s = 0; s < g_arm_power.nsock; s++) {
    char dev[16];
    struct stats *stats;

    snprintf(dev, sizeof(dev), "%d", s);
    stats = get_current_stats(type, dev);
    if (stats != NULL)
      arm_power_socket_publish(stats, &g_arm_power, g_arm_power_slots[s]);
  }
}

struct stats_type arm_power_socket_stats_type = {
    .st_begin = &arm_power_socket_begin,
    .st_collect = &arm_power_socket_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(ARM_POWER_SOCKET_KEYS),
#undef X
    .st_name = ARM_POWER_SOCKET_ST_NAME,
};
