/* Unit tests for opa_mad_dyn (no real liboib_utils required). */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "opa_mad_dyn.h"

static int g_fake_open_calls;

static int fake_oib_open_port_by_num(struct oib_port **port, uint8 hfi, uint32 port_num)
{
  (void)hfi;
  (void)port_num;
  g_fake_open_calls++;
  if (port != NULL)
    *port = (struct oib_port *)(uintptr_t)0x1;
  return 0;
}

static void fake_oib_close_port(struct oib_port *port)
{
  (void)port;
}

static void test_load_missing_lib_fails(void)
{
  opa_mad_dyn_unload();
  setenv("HPCPERFSTATS_OIB_LIB", "/nonexistent/liboib_utils.so.test-missing", 1);
  assert(opa_mad_dyn_load() < 0);
  assert(opa_mad_dyn_loaded() == 0);
  assert(opa_mad_dyn_last_error()[0] != '\0');
  unsetenv("HPCPERFSTATS_OIB_LIB");
}

static void test_injected_hooks(void)
{
  struct opa_mad_dyn_test_hooks hooks;
  struct oib_port *port = NULL;

  opa_mad_dyn_unload();
  memset(&hooks, 0, sizeof(hooks));
  hooks.oib_open_port_by_num = fake_oib_open_port_by_num;
  hooks.oib_close_port = fake_oib_close_port;
  opa_mad_dyn_test_set_hooks(&hooks);

  g_fake_open_calls = 0;
  assert(opa_mad_dyn_oib_open_port_by_num(&port, 0, 1) == 0);
  assert(g_fake_open_calls == 1);
  assert(port != NULL);
  opa_mad_dyn_oib_close_port(port);

  opa_mad_dyn_test_set_hooks(NULL);
}

static void test_idempotent_load_failure(void)
{
  opa_mad_dyn_unload();
  setenv("HPCPERFSTATS_OIB_LIB", "/nonexistent/liboib_utils.so.test-missing", 1);
  assert(opa_mad_dyn_load() < 0);
  assert(opa_mad_dyn_load() < 0);
  unsetenv("HPCPERFSTATS_OIB_LIB");
}

int main(void)
{
  test_load_missing_lib_fails();
  test_injected_hooks();
  test_idempotent_load_failure();
  printf("test_opa_mad_dyn passed\n");
  return 0;
}
