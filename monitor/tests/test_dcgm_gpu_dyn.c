/* Unit tests for dcgm_gpu_dyn (no real libdcgm required). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dcgm_agent.h"
#include "dcgm_gpu_dyn.h"

static int g_fake_init_calls;

static dcgmReturn_t fake_dcgmInit(void)
{
  g_fake_init_calls++;
  return DCGM_ST_OK;
}

static const char *fake_errorString(dcgmReturn_t result)
{
  (void)result;
  return "fake-dcgm-error";
}

static void test_not_loaded_init_fails(void)
{
  dcgm_gpu_dyn_unload();
  assert(dcgm_gpu_dyn_loaded() == 0);
  assert(dcgm_gpu_dyn_dcgmInit() == DCGM_ST_UNINITIALIZED);
}

static void test_load_missing_lib_fails(void)
{
  dcgm_gpu_dyn_unload();
  setenv("HPCPERFSTATS_DCGM_LIB", "/nonexistent/libdcgm.so.test-missing", 1);
  assert(dcgm_gpu_dyn_load() < 0);
  assert(dcgm_gpu_dyn_loaded() == 0);
  assert(strstr(dcgm_gpu_dyn_last_error(), "nonexistent") != NULL ||
         dcgm_gpu_dyn_last_error()[0] != '\0');
  unsetenv("HPCPERFSTATS_DCGM_LIB");
}

static void test_injected_hooks(void)
{
  struct dcgm_gpu_dyn_test_hooks hooks;

  dcgm_gpu_dyn_unload();
  memset(&hooks, 0, sizeof(hooks));
  hooks.dcgmInit = fake_dcgmInit;
  hooks.errorString = fake_errorString;
  dcgm_gpu_dyn_test_set_hooks(&hooks);

  g_fake_init_calls = 0;
  assert(dcgm_gpu_dyn_dcgmInit() == DCGM_ST_OK);
  assert(g_fake_init_calls == 1);
  assert(strcmp(dcgm_gpu_dyn_errorString(DCGM_ST_OK), "fake-dcgm-error") == 0);

  dcgm_gpu_dyn_test_set_hooks(NULL);
}

/* Defined in dcgm_gpu_dyn.c (non-static) so tests can check that unload drops injected hooks. */
extern struct dcgm_gpu_dyn_test_hooks g_test_hooks_storage;

/* Regression: unload used memset on the g_test_hooks pointer, leaving stale fake functions in
 * g_test_hooks_storage. Unload must drop all injected hooks. */
static void test_unload_clears_injected_hooks(void)
{
  static const struct dcgm_gpu_dyn_test_hooks zero;
  struct dcgm_gpu_dyn_test_hooks hooks;

  memset(&hooks, 0, sizeof(hooks));
  hooks.dcgmInit = fake_dcgmInit;
  hooks.errorString = fake_errorString;
  dcgm_gpu_dyn_test_set_hooks(&hooks);

  dcgm_gpu_dyn_unload();

  g_fake_init_calls = 0;
  assert(dcgm_gpu_dyn_dcgmInit() == DCGM_ST_UNINITIALIZED);
  assert(g_fake_init_calls == 0);
  assert(memcmp(&g_test_hooks_storage, &zero, sizeof(zero)) == 0);
}

static void test_idempotent_load_failure(void)
{
  dcgm_gpu_dyn_unload();
  setenv("HPCPERFSTATS_DCGM_LIB", "/nonexistent/libdcgm.so.test-missing", 1);
  assert(dcgm_gpu_dyn_load() < 0);
  assert(dcgm_gpu_dyn_load() < 0);
  unsetenv("HPCPERFSTATS_DCGM_LIB");
}

int main(void)
{
  test_not_loaded_init_fails();
  test_load_missing_lib_fails();
  test_injected_hooks();
  test_unload_clears_injected_hooks();
  test_idempotent_load_failure();
  printf("test_dcgm_gpu_dyn passed\n");
  return 0;
}
