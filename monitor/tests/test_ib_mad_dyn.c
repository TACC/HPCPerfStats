/* Unit tests for ib_mad_dyn (no real libibmad required). */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ib_mad_dyn.h"

static int g_fake_open_calls;

static struct ibmad_port *fake_mad_rpc_open_port(char *dev_name, int dev_port,
                                                 int *mgmt_classes, int num_classes)
{
  (void) dev_name;
  (void) dev_port;
  (void) mgmt_classes;
  (void) num_classes;
  g_fake_open_calls++;
  return (struct ibmad_port *) (uintptr_t) 0x1;
}

static void fake_mad_rpc_close_port(struct ibmad_port *srcport)
{
  (void) srcport;
}

static void test_load_missing_lib_fails(void)
{
  ib_mad_dyn_unload();
  setenv("HPCPERFSTATS_IBMAD_LIB", "/nonexistent/libibmad.so.test-missing", 1);
  assert(ib_mad_dyn_load() < 0);
  assert(ib_mad_dyn_loaded() == 0);
  assert(ib_mad_dyn_last_error()[0] != '\0');
  unsetenv("HPCPERFSTATS_IBMAD_LIB");
}

static void test_injected_hooks(void)
{
  struct ib_mad_dyn_test_hooks hooks;

  ib_mad_dyn_unload();
  memset(&hooks, 0, sizeof(hooks));
  hooks.mad_rpc_open_port = fake_mad_rpc_open_port;
  hooks.mad_rpc_close_port = fake_mad_rpc_close_port;
  ib_mad_dyn_test_set_hooks(&hooks);

  g_fake_open_calls = 0;
  assert(ib_mad_dyn_mad_rpc_open_port((char *) "mlx5_0", 1, NULL, 0) != NULL);
  assert(g_fake_open_calls == 1);
  ib_mad_dyn_mad_rpc_close_port(NULL);

  ib_mad_dyn_test_set_hooks(NULL);
}

static void test_idempotent_load_failure(void)
{
  ib_mad_dyn_unload();
  setenv("HPCPERFSTATS_IBMAD_LIB", "/nonexistent/libibmad.so.test-missing", 1);
  assert(ib_mad_dyn_load() < 0);
  assert(ib_mad_dyn_load() < 0);
  unsetenv("HPCPERFSTATS_IBMAD_LIB");
}

int main(void)
{
  test_load_missing_lib_fails();
  test_injected_hooks();
  test_idempotent_load_failure();
  printf("test_ib_mad_dyn passed\n");
  return 0;
}
