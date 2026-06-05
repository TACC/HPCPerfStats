/* host_ib — InfiniBand HCA port counters from sysfs (no MAD/verbs). */
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
#include "ib.h"

/* IB_PORT_ACTIVE == 4; sysfs may print "4: ACTIVE", "active", or "inactive". */
static int ib_port_logic_active(const char *state_line)
{
  const char *p;
  char *endp = NULL;
  unsigned long v;

  if (state_line == NULL)
    return 0;
  p = state_line;
  while (*p != '\0' && isspace((unsigned char) *p))
    p++;
  v = strtoul(p, &endp, 10);
  if (endp != p && v == 4)
    return 1;
  if (strstr(state_line, "active") != NULL)
    return 1;
  return 0;
}

/* IB_LINK_LAYER_ACTIVE / LinkUp wording varies by kernel. */
static int ib_port_phys_link_up(const char *phys_line)
{
  const char *p;
  char *endp = NULL;
  unsigned long v;

  if (phys_line == NULL)
    return 0;
  p = phys_line;
  while (*p != '\0' && isspace((unsigned char) *p))
    p++;
  v = strtoul(p, &endp, 10);
  if (endp != p && v == 5)
    return 1;
  if (strstr(phys_line, "link_up") != NULL || strstr(phys_line, "linkup") != NULL)
    return 1;
  return 0;
}

static int ib_port_read_state_file(const char *path, char *buf, size_t buf_len)
{
  FILE *f;

  if (path == NULL || buf == NULL || buf_len == 0)
    return 0;
  f = path_file_fopen_read(path);
  if (f == NULL)
    return 0;
  if (fgets(buf, (int) buf_len, f) == NULL) {
    fclose(f);
    return 0;
  }
  fclose(f);
  return 1;
}

static int ib_port_collectible(const char *hca, int port)
{
  char path[160];
  char buf[96];

  if (hca == NULL)
    return 0;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/state", hca, port);
  if (ib_port_read_state_file(path, buf, sizeof(buf)) && ib_port_logic_active(buf))
    return 1;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/phys_state", hca, port);
  if (!ib_port_read_state_file(path, buf, sizeof(buf)))
    return 0;
  return ib_port_phys_link_up(buf);
}

static void ib_merge_counters_dir(struct stats *stats, const char *dir_path)
{
  if (stats == NULL || dir_path == NULL)
    return;
  (void) path_collect_key_value_dir(dir_path, stats);
}

static void ib_collect_port_lid(struct stats *stats, const char *dev, int port)
{
  char path[160];
  FILE *file;
  char file_buf[4096];
  unsigned int lid;

  if (stats == NULL || dev == NULL)
    return;

  snprintf(path, sizeof(path), "/sys/class/infiniband/%s/ports/%d/lid", dev, port);
  file = path_file_fopen_read(path);
  if (file == NULL)
    return;
  setvbuf(file, file_buf, _IOFBF, sizeof(file_buf));
  if (fscanf(file, "%x", &lid) != 1)
    (void) lid;
  fclose(file);
}

static void ib_collect_port(struct stats_type *type, const char *dev, int port)
{
  char path[160];
  char id[80];
  struct stats *stats;

  if (type == NULL || dev == NULL)
    return;
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
  ib_collect_port_lid(stats, dev, port);
}

struct ib_port_ctx {
  struct stats_type *type;
  const char *dev;
};

static void ib_collect_port_each(const char *base, const char *name, void *ctx)
{
  struct ib_port_ctx *pc = (struct ib_port_ctx *) ctx;
  char *endp = NULL;
  long pn;

  (void) base;
  if (pc == NULL || name == NULL)
    return;
  pn = strtol(name, &endp, 10);
  if (endp == name || *endp != '\0')
    return;
  if (pn < 1 || pn > INT_MAX)
    return;
  ib_collect_port(pc->type, pc->dev, (int) pn);
}

static void ib_collect_dev(struct stats_type *type, const char *dev)
{
  char ports_path[160];
  struct ib_port_ctx pc = { type, dev };

  if (type == NULL || dev == NULL)
    return;
  snprintf(ports_path, sizeof(ports_path), "/sys/class/infiniband/%s/ports", dev);
  sys_iter_for_each(ports_path, ib_collect_port_each, &pc);
}

static void ib_collect_each(const char *base, const char *name, void *ctx)
{
  (void) base;
  if (name == NULL)
    return;
  ib_collect_dev((struct stats_type *) ctx, name);
}

static void ib_collect(struct stats_type *type)
{
  if (type == NULL)
    return;
  sys_iter_for_each("/sys/class/infiniband", ib_collect_each, type);
}

struct stats_type ib_stats_type = {
  .st_name = "host_ib",
  .st_collect = &ib_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
