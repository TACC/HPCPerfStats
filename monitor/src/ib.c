#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <dirent.h>
#include <limits.h>
#include "stats.h"
#include "collect.h"
#include "fileio.h"
#include "trace.h"
#include "path_open_fail_once.h"
#include "sys_iter.h"

// const char *perfquery = "/opt/ofed/sbin/perfquery";

#define KEYS \
  X(excessive_buffer_overrun_errors, "E,W=32", ""), \
  X(link_downed, "E,W=32", "failed link error recoveries"), \
  X(link_error_recovery, "E,W=32", "successful link error recoveries"), \
  X(local_link_integrity_errors, "E,W=32", ""), \
  X(port_rcv_constraint_errors, "E,W=32", "packets discarded due to constraint"), \
  X(port_rcv_data, "E,W=32,U=4B", "data received"), \
  X(port_rcv_errors, "E,W=32", "bad packets received"), \
  X(port_rcv_packets, "E,W=32", "packets received"), \
  X(port_rcv_remote_physical_errors, "E,W=32", "EBP packets received"), \
  X(port_rcv_switch_relay_errors, "E,W=32", ""), \
  X(port_xmit_constraint_errors, "E,W=32", "packets not transmitted due to constraint"), \
  X(port_xmit_data, "E,W=32,U=4B", "data transmitted"), \
  X(port_xmit_discards, "E,W=32", "packets discarded due to down or congested port"), \
  X(port_xmit_packets, "E,W=32", "packets transmitted"), \
  X(port_xmit_wait, "E,W=32,U=ms", "wait time for credits or arbitration"), \
  X(symbol_error, "E,W=32", "minor link errors"), \
  X(VL15_dropped, "E,W=32", "")

/* IB_PORT_ACTIVE == 4; sysfs may print "4: ACTIVE", "ACTIVE", or "Active". */
static int ib_port_logic_active(const char *state_line)
{
  const char *p = state_line;
  char *endp = NULL;
  unsigned long v;

  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  v = strtoul(p, &endp, 10);
  if (endp != p && v == 4)
    return 1;
  if (strstr(state_line, "ACTIVE") != NULL)
    return 1;
  if (strstr(state_line, "Active") != NULL && strstr(state_line, "Inactive") == NULL)
    return 1;
  return 0;
}

/* IB_LINK_LAYER_ACTIVE / LinkUp wording varies by kernel. */
static int ib_port_phys_link_up(const char *phys_line)
{
  const char *p = phys_line;
  char *endp = NULL;
  unsigned long v;

  while (*p != '\0' && isspace((unsigned char)*p))
    p++;
  v = strtoul(p, &endp, 10);
  if (endp != p && v == 5)
    return 1;
  if (strstr(phys_line, "LinkUp") != NULL || strstr(phys_line, "linkup") != NULL)
    return 1;
  return 0;
}

static int ib_port_collectible(const char *hca, int port)
{
  char path[160];
  char buf[96];
  FILE *f;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/state", hca, port);
  f = path_file_fopen_read(path);
  if (f != NULL) {
    if (fgets(buf, sizeof(buf), f) != NULL) {
      fclose(f);
      if (ib_port_logic_active(buf))
        return 1;
    } else
      fclose(f);
  }

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/phys_state", hca, port);
  f = path_file_fopen_read(path);
  if (f == NULL)
    return 0;
  if (fgets(buf, sizeof(buf), f) == NULL) {
    fclose(f);
    return 0;
  }
  fclose(f);
  return ib_port_phys_link_up(buf);
}

static void ib_merge_counters_dir(struct stats *stats, const char *dir_path)
{
  (void) path_collect_key_value_dir(dir_path, stats);
}

static void ib_collect_port(struct stats_type *type, const char *dev, int port)
{
  char path[160], id[80];
  FILE *file = NULL;
  char file_buf[4096];
  unsigned int lid;
  struct stats *stats = NULL;

  if (!ib_port_collectible(dev, port))
    return;

  TRACE("dev %s, port %i\n", dev, port);

  snprintf(id, sizeof(id), "%s.%i", dev, port);
  stats = get_current_stats(type, id);
  if (stats == NULL)
    return;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/counters", dev, port);
  ib_merge_counters_dir(stats, path);
  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/hw_counters", dev, port);
  ib_merge_counters_dir(stats, path);

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/lid", dev, port);
  file = path_file_fopen_read(path);
  if (file == NULL)
    return;
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));

  if (fscanf(file, "%x", &lid) != 1) {
    fclose(file);
    return;
  }

  fclose(file);
}

struct ib_port_ctx {
  struct stats_type *type;
  const char *dev;
};

static void ib_collect_port_each(const char *base, const char *name, void *ctx)
{
  struct ib_port_ctx *pc = (struct ib_port_ctx *)ctx;
  char *endp = NULL;
  long pn;

  (void)base;
  pn = strtol(name, &endp, 10);
  if (endp == name || *endp != '\0')
    return;
  if (pn < 1 || pn > INT_MAX)
    return;
  ib_collect_port(pc->type, pc->dev, (int)pn);
}

static void ib_collect_dev(struct stats_type *type, const char *dev)
{
  char ports_path[160];
  struct ib_port_ctx pc = { type, dev };

  snprintf(ports_path, sizeof(ports_path), "/sys/class/infiniband/%s/ports", dev);
  sys_iter_for_each(ports_path, ib_collect_port_each, &pc);
}

static void ib_collect_each(const char *base, const char *name, void *ctx)
{
  (void)base;
  ib_collect_dev((struct stats_type *)ctx, name);
}

static void ib_collect(struct stats_type *type)
{
  sys_iter_for_each("/sys/class/infiniband", ib_collect_each, type);
}

struct stats_type ib_stats_type = {
  .st_name = "ib",
  .st_collect = &ib_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
