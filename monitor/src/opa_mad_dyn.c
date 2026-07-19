/* opa_mad_dyn — runtime dlopen of liboib_utils for host_opa STL MAD. */
#include "opa_mad_dyn.h"

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define OPA_MAD_DYN_SYM_LIST \
  X(oib_open_port_by_num) \
  X(oib_close_port) \
  X(oib_get_port_state) \
  X(oib_get_port_lid) \
  X(oib_get_mgmt_pkey) \
  X(oib_send_recv_mad_no_alloc)

#define X(name) static __typeof__(name) *p_##name;
OPA_MAD_DYN_SYM_LIST
#undef X

static void *g_oib_handle;
static int g_oib_loaded;
static char g_oib_last_error[256];

struct opa_mad_dyn_test_hooks g_opa_mad_test_hooks_storage;
static struct opa_mad_dyn_test_hooks *g_opa_mad_test_hooks;
static int g_opa_mad_test_hooks_active;

static void opa_mad_dyn_set_error(const char *msg)
{
  if (msg == NULL)
    msg = "unknown error";
  snprintf(g_oib_last_error, sizeof(g_oib_last_error), "%s", msg);
}

const char *opa_mad_dyn_last_error(void)
{
  return g_oib_last_error[0] != '\0' ? g_oib_last_error
                                     : "opa_mad_dyn: no error recorded";
}

static int opa_mad_dyn_resolve_one(void *lib, const char *sym, void **out)
{
  void *fn;

  if (out == NULL)
    return -1;
  *out = NULL;
  fn = dlsym(lib, sym);
  if (fn == NULL) {
    opa_mad_dyn_set_error(sym);
    return -1;
  }
  *out = fn;
  return 0;
}

static int opa_mad_dyn_try_open(const char *path)
{
  void *h;

  if (path == NULL || path[0] == '\0')
    return -1;
  h = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
  if (h == NULL) {
    opa_mad_dyn_set_error(dlerror());
    return -1;
  }
  g_oib_handle = h;
  return 0;
}

static int opa_mad_dyn_bind_symbols(void)
{
  void *lib = g_oib_handle;

#define X(name) \
  if (opa_mad_dyn_resolve_one(lib, #name, (void **) &p_##name) < 0) \
    return -1;
  OPA_MAD_DYN_SYM_LIST
#undef X
  return 0;
}

void opa_mad_dyn_test_set_hooks(const struct opa_mad_dyn_test_hooks *hooks)
{
  memset(&g_opa_mad_test_hooks_storage, 0, sizeof(g_opa_mad_test_hooks_storage));
  g_opa_mad_test_hooks = NULL;
  g_opa_mad_test_hooks_active = 0;
  if (hooks != NULL) {
    g_opa_mad_test_hooks_storage = *hooks;
    g_opa_mad_test_hooks = &g_opa_mad_test_hooks_storage;
    g_opa_mad_test_hooks_active = 1;
  }
}

int opa_mad_dyn_load(void)
{
  static const char *default_libs[] = {
    "liboib_utils.so",
    "liboib_utils.so.1",
    NULL
  };
  const char *override;
  size_t i;

  if (g_oib_loaded)
    return 0;

  g_oib_last_error[0] = '\0';
  override = getenv("HPCPERFSTATS_OIB_LIB");
  if (override != NULL && override[0] != '\0') {
    if (opa_mad_dyn_try_open(override) < 0)
      return -1;
  } else {
    for (i = 0; default_libs[i] != NULL; i++) {
      g_oib_last_error[0] = '\0';
      if (opa_mad_dyn_try_open(default_libs[i]) == 0)
        break;
    }
    if (g_oib_handle == NULL)
      return -1;
  }

  if (opa_mad_dyn_bind_symbols() < 0) {
    dlclose(g_oib_handle);
    g_oib_handle = NULL;
    return -1;
  }

  g_oib_loaded = 1;
  return 0;
}

int opa_mad_dyn_loaded(void)
{
  return g_oib_loaded;
}

void opa_mad_dyn_unload(void)
{
  if (g_oib_handle != NULL) {
    dlclose(g_oib_handle);
    g_oib_handle = NULL;
  }
  g_oib_loaded = 0;
  g_opa_mad_test_hooks_active = 0;
  g_opa_mad_test_hooks = NULL;
#define X(name) p_##name = NULL;
  OPA_MAD_DYN_SYM_LIST
#undef X
}

int opa_mad_dyn_oib_open_port_by_num(struct oib_port **port, uint8 hfi,
                                     uint32 port_num)
{
  if (g_opa_mad_test_hooks_active && g_opa_mad_test_hooks != NULL
      && g_opa_mad_test_hooks->oib_open_port_by_num != NULL)
    return g_opa_mad_test_hooks->oib_open_port_by_num(port, hfi, port_num);
  if (!g_oib_loaded && opa_mad_dyn_load() < 0)
    return -1;
  if (p_oib_open_port_by_num == NULL)
    return -1;
  return p_oib_open_port_by_num(port, hfi, port_num);
}

void opa_mad_dyn_oib_close_port(struct oib_port *port)
{
  if (g_opa_mad_test_hooks_active && g_opa_mad_test_hooks != NULL
      && g_opa_mad_test_hooks->oib_close_port != NULL) {
    g_opa_mad_test_hooks->oib_close_port(port);
    return;
  }
  if (p_oib_close_port != NULL)
    p_oib_close_port(port);
}

int opa_mad_dyn_oib_get_port_state(struct oib_port *port)
{
  if (g_opa_mad_test_hooks_active && g_opa_mad_test_hooks != NULL
      && g_opa_mad_test_hooks->oib_get_port_state != NULL)
    return g_opa_mad_test_hooks->oib_get_port_state(port);
  if (!g_oib_loaded && opa_mad_dyn_load() < 0)
    return 0;
  if (p_oib_get_port_state == NULL)
    return 0;
  return p_oib_get_port_state(port);
}

IB_LID opa_mad_dyn_oib_get_port_lid(struct oib_port *port)
{
  if (g_opa_mad_test_hooks_active && g_opa_mad_test_hooks != NULL
      && g_opa_mad_test_hooks->oib_get_port_lid != NULL)
    return g_opa_mad_test_hooks->oib_get_port_lid(port);
  if (!g_oib_loaded && opa_mad_dyn_load() < 0)
    return 0;
  if (p_oib_get_port_lid == NULL)
    return 0;
  return p_oib_get_port_lid(port);
}

uint16_t opa_mad_dyn_oib_get_mgmt_pkey(struct oib_port *port, IB_LID lid,
                                       uint8_t hop)
{
  if (g_opa_mad_test_hooks_active && g_opa_mad_test_hooks != NULL
      && g_opa_mad_test_hooks->oib_get_mgmt_pkey != NULL)
    return g_opa_mad_test_hooks->oib_get_mgmt_pkey(port, lid, hop);
  if (!g_oib_loaded && opa_mad_dyn_load() < 0)
    return 0;
  if (p_oib_get_mgmt_pkey == NULL)
    return 0;
  return p_oib_get_mgmt_pkey(port, lid, hop);
}

int opa_mad_dyn_oib_send_recv_mad_no_alloc(struct oib_port *port,
                                           uint8_t *send_buf, size_t send_size,
                                           struct oib_mad_addr *addr,
                                           uint8_t *recv_buf, size_t *recv_size,
                                           unsigned timeout_ms, unsigned flags)
{
  if (g_opa_mad_test_hooks_active && g_opa_mad_test_hooks != NULL
      && g_opa_mad_test_hooks->oib_send_recv_mad_no_alloc != NULL)
    return g_opa_mad_test_hooks->oib_send_recv_mad_no_alloc(
        port, send_buf, send_size, addr, recv_buf, recv_size, timeout_ms, flags);
  if (!g_oib_loaded && opa_mad_dyn_load() < 0)
    return -1;
  if (p_oib_send_recv_mad_no_alloc == NULL)
    return -1;
  return p_oib_send_recv_mad_no_alloc(port, send_buf, send_size, addr, recv_buf,
                                      recv_size, timeout_ms, flags);
}
