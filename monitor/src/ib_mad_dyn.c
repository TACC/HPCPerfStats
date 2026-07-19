/* ib_mad_dyn — runtime dlopen of libibmad for host_ib MAD (no link-time -libmad). */
#include "ib_mad_dyn.h"

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define IB_MAD_DYN_SYM_LIST \
  X(mad_rpc_open_port) \
  X(mad_rpc_close_port) \
  X(pma_query_via) \
  X(smp_query_via) \
  X(mad_decode_field)

#define X(name) static __typeof__(name) *p_##name;
IB_MAD_DYN_SYM_LIST
#undef X

static void *g_ibmad_handle;
static int g_ibmad_loaded;
static char g_ibmad_last_error[256];

struct ib_mad_dyn_test_hooks g_ib_mad_test_hooks_storage;
static struct ib_mad_dyn_test_hooks *g_ib_mad_test_hooks;
static int g_ib_mad_test_hooks_active;

static void ib_mad_dyn_set_error(const char *msg)
{
  if (msg == NULL)
    msg = "unknown error";
  snprintf(g_ibmad_last_error, sizeof(g_ibmad_last_error), "%s", msg);
}

const char *ib_mad_dyn_last_error(void)
{
  return g_ibmad_last_error[0] != '\0' ? g_ibmad_last_error
                                       : "ib_mad_dyn: no error recorded";
}

static int ib_mad_dyn_resolve_one(void *lib, const char *sym, void **out)
{
  void *fn;

  if (out == NULL)
    return -1;
  *out = NULL;
  fn = dlsym(lib, sym);
  if (fn == NULL) {
    ib_mad_dyn_set_error(sym);
    return -1;
  }
  *out = fn;
  return 0;
}

static int ib_mad_dyn_try_open(const char *path)
{
  void *h;

  if (path == NULL || path[0] == '\0')
    return -1;
  h = dlopen(path, RTLD_LAZY | RTLD_LOCAL);
  if (h == NULL) {
    ib_mad_dyn_set_error(dlerror());
    return -1;
  }
  g_ibmad_handle = h;
  return 0;
}

static int ib_mad_dyn_bind_symbols(void)
{
  void *lib = g_ibmad_handle;

#define X(name) \
  if (ib_mad_dyn_resolve_one(lib, #name, (void **) &p_##name) < 0) \
    return -1;
  IB_MAD_DYN_SYM_LIST
#undef X
  return 0;
}

void ib_mad_dyn_test_set_hooks(const struct ib_mad_dyn_test_hooks *hooks)
{
  memset(&g_ib_mad_test_hooks_storage, 0, sizeof(g_ib_mad_test_hooks_storage));
  g_ib_mad_test_hooks = NULL;
  g_ib_mad_test_hooks_active = 0;
  if (hooks != NULL) {
    g_ib_mad_test_hooks_storage = *hooks;
    g_ib_mad_test_hooks = &g_ib_mad_test_hooks_storage;
    g_ib_mad_test_hooks_active = 1;
  }
}

int ib_mad_dyn_load(void)
{
  static const char *default_libs[] = {
    "libibmad.so.5",
    "libibmad.so",
    NULL
  };
  const char *override;
  size_t i;

  if (g_ibmad_loaded)
    return 0;

  g_ibmad_last_error[0] = '\0';
  override = getenv("HPCPERFSTATS_IBMAD_LIB");
  if (override != NULL && override[0] != '\0') {
    if (ib_mad_dyn_try_open(override) < 0)
      return -1;
  } else {
    for (i = 0; default_libs[i] != NULL; i++) {
      g_ibmad_last_error[0] = '\0';
      if (ib_mad_dyn_try_open(default_libs[i]) == 0)
        break;
    }
    if (g_ibmad_handle == NULL)
      return -1;
  }

  if (ib_mad_dyn_bind_symbols() < 0) {
    dlclose(g_ibmad_handle);
    g_ibmad_handle = NULL;
    return -1;
  }

  g_ibmad_loaded = 1;
  return 0;
}

int ib_mad_dyn_loaded(void)
{
  return g_ibmad_loaded;
}

void ib_mad_dyn_unload(void)
{
  if (g_ibmad_handle != NULL) {
    dlclose(g_ibmad_handle);
    g_ibmad_handle = NULL;
  }
  g_ibmad_loaded = 0;
  g_ib_mad_test_hooks_active = 0;
  g_ib_mad_test_hooks = NULL;
#define X(name) p_##name = NULL;
  IB_MAD_DYN_SYM_LIST
#undef X
}

struct ibmad_port *ib_mad_dyn_mad_rpc_open_port(char *dev_name, int dev_port,
                                                int *mgmt_classes,
                                                int num_classes)
{
  if (g_ib_mad_test_hooks_active && g_ib_mad_test_hooks != NULL
      && g_ib_mad_test_hooks->mad_rpc_open_port != NULL)
    return g_ib_mad_test_hooks->mad_rpc_open_port(dev_name, dev_port,
                                                  mgmt_classes, num_classes);
  if (!g_ibmad_loaded && ib_mad_dyn_load() < 0)
    return NULL;
  if (p_mad_rpc_open_port == NULL)
    return NULL;
  return p_mad_rpc_open_port(dev_name, dev_port, mgmt_classes, num_classes);
}

void ib_mad_dyn_mad_rpc_close_port(struct ibmad_port *srcport)
{
  if (g_ib_mad_test_hooks_active && g_ib_mad_test_hooks != NULL
      && g_ib_mad_test_hooks->mad_rpc_close_port != NULL) {
    g_ib_mad_test_hooks->mad_rpc_close_port(srcport);
    return;
  }
  if (p_mad_rpc_close_port != NULL)
    p_mad_rpc_close_port(srcport);
}

uint8_t *ib_mad_dyn_pma_query_via(void *rcvbuf, ib_portid_t *dest, int port,
                                  unsigned timeout, unsigned id,
                                  const struct ibmad_port *srcport)
{
  if (g_ib_mad_test_hooks_active && g_ib_mad_test_hooks != NULL
      && g_ib_mad_test_hooks->pma_query_via != NULL)
    return g_ib_mad_test_hooks->pma_query_via(rcvbuf, dest, port, timeout, id,
                                              srcport);
  if (!g_ibmad_loaded && ib_mad_dyn_load() < 0)
    return NULL;
  if (p_pma_query_via == NULL)
    return NULL;
  return p_pma_query_via(rcvbuf, dest, port, timeout, id, srcport);
}

uint8_t *ib_mad_dyn_smp_query_via(void *buf, ib_portid_t *id, unsigned attrid,
                                  unsigned mod, unsigned timeout,
                                  const struct ibmad_port *srcport)
{
  if (g_ib_mad_test_hooks_active && g_ib_mad_test_hooks != NULL
      && g_ib_mad_test_hooks->smp_query_via != NULL)
    return g_ib_mad_test_hooks->smp_query_via(buf, id, attrid, mod, timeout,
                                              srcport);
  if (!g_ibmad_loaded && ib_mad_dyn_load() < 0)
    return NULL;
  if (p_smp_query_via == NULL)
    return NULL;
  return p_smp_query_via(buf, id, attrid, mod, timeout, srcport);
}

void ib_mad_dyn_mad_decode_field(uint8_t *buf, enum MAD_FIELDS field, void *val)
{
  if (g_ib_mad_test_hooks_active && g_ib_mad_test_hooks != NULL
      && g_ib_mad_test_hooks->mad_decode_field != NULL) {
    g_ib_mad_test_hooks->mad_decode_field(buf, field, val);
    return;
  }
  if (!g_ibmad_loaded && ib_mad_dyn_load() < 0)
    return;
  if (p_mad_decode_field != NULL)
    p_mad_decode_field(buf, field, val);
}
