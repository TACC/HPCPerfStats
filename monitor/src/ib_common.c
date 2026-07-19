/* Shared InfiniBand HCA/port discovery for the host_ib stats family. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <limits.h>
#include <dirent.h>

#include "path_open_fail_once.h"
#include "sys_iter.h"
#include "ib_common.h"
#include "ib_port_state.h"

int ib_hca_is_opa_hfi(const char *hca)
{
  if (hca == NULL || hca[0] == '\0')
    return 0;
  /* Linux hfi1 driver: hfi1_0, hfi1_1, … (Cornelis CN5000 and Intel OPA 100). */
  if (strncmp(hca, "hfi1", 4) != 0)
    return 0;
  return hca[4] == '\0' || hca[4] == '_';
}

int ib_sysfs_has_opa_hfi(void)
{
  DIR *d;
  struct dirent *ent;
  int found = 0;

  d = opendir("/sys/class/infiniband");
  if (d == NULL)
    return 0;
  while ((ent = readdir(d)) != NULL) {
    if (ent->d_name[0] == '.')
      continue;
    if (ib_hca_is_opa_hfi(ent->d_name)) {
      found = 1;
      break;
    }
  }
  closedir(d);
  return found;
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

int ib_port_collectible(const char *hca, int port)
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

struct ib_port_iter_ctx {
  ib_hca_port_fn fn;
  void *user_ctx;
  const char *hca;
};

static void ib_port_iter_each(const char *base, const char *name, void *ctx)
{
  struct ib_port_iter_ctx *pc = (struct ib_port_iter_ctx *) ctx;
  char *endp = NULL;
  long pn;

  (void) base;
  if (pc == NULL || pc->fn == NULL || name == NULL)
    return;
  pn = strtol(name, &endp, 10);
  if (endp == name || *endp != '\0')
    return;
  if (pn < 1 || pn > INT_MAX)
    return;
  if (!ib_port_collectible(pc->hca, (int) pn))
    return;
  pc->fn(pc->hca, (int) pn, pc->user_ctx);
}

struct ib_hca_iter_ctx {
  ib_hca_port_fn fn;
  void *user_ctx;
};

static void ib_hca_iter_each(const char *base, const char *name, void *ctx)
{
  struct ib_hca_iter_ctx *hc = (struct ib_hca_iter_ctx *) ctx;
  char ports_path[160];
  struct ib_port_iter_ctx pc;

  if (hc == NULL || hc->fn == NULL || name == NULL)
    return;
  if (ib_hca_is_opa_hfi(name))
    return;
  snprintf(ports_path, sizeof(ports_path), "%s/%s/ports", base, name);
  pc.fn = hc->fn;
  pc.user_ctx = hc->user_ctx;
  pc.hca = name;
  sys_iter_for_each(ports_path, ib_port_iter_each, &pc);
}

void ib_foreach_hca_port(ib_hca_port_fn fn, void *ctx)
{
  struct ib_hca_iter_ctx hc = { fn, ctx };

  if (fn == NULL)
    return;
  sys_iter_for_each("/sys/class/infiniband", ib_hca_iter_each, &hc);
}
