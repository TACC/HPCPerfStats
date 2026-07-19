/* Unit tests for xpum_gpu_dyn (no real libxpum required). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "xpum_gpu_dyn.h"

static int g_fake_init_calls;

static xpum_result_t fake_xpumInit(void)
{
  g_fake_init_calls++;
  return XPUM_OK;
}

static xpum_result_t fake_xpumShutdown(void)
{
  return XPUM_OK;
}

static void test_not_loaded_init_fails(void)
{
  xpum_gpu_dyn_unload();
  assert(xpum_gpu_dyn_loaded() == 0);
  assert(xpum_gpu_dyn_xpumInit() == XPUM_GENERIC_ERROR);
}

static void test_load_missing_lib_fails(void)
{
  xpum_gpu_dyn_unload();
  setenv("HPCPERFSTATS_XPUM_LIB", "/nonexistent/libxpum.so.test-missing", 1);
  assert(xpum_gpu_dyn_load() < 0);
  assert(xpum_gpu_dyn_loaded() == 0);
  assert(xpum_gpu_dyn_last_error()[0] != '\0');
  unsetenv("HPCPERFSTATS_XPUM_LIB");
}

static void test_injected_hooks(void)
{
  struct xpum_gpu_dyn_test_hooks hooks;

  xpum_gpu_dyn_unload();
  memset(&hooks, 0, sizeof(hooks));
  hooks.xpumInit = fake_xpumInit;
  hooks.xpumShutdown = fake_xpumShutdown;
  xpum_gpu_dyn_test_set_hooks(&hooks);

  g_fake_init_calls = 0;
  assert(xpum_gpu_dyn_xpumInit() == XPUM_OK);
  assert(g_fake_init_calls == 1);
  assert(xpum_gpu_dyn_xpumShutdown() == XPUM_OK);

  xpum_gpu_dyn_test_set_hooks(NULL);
}

static void test_idempotent_load_failure(void)
{
  xpum_gpu_dyn_unload();
  setenv("HPCPERFSTATS_XPUM_LIB", "/nonexistent/libxpum.so.test-missing", 1);
  assert(xpum_gpu_dyn_load() < 0);
  assert(xpum_gpu_dyn_load() < 0);
  unsetenv("HPCPERFSTATS_XPUM_LIB");
}

int main(void)
{
  test_not_loaded_init_fails();
  test_load_missing_lib_fails();
  test_injected_hooks();
  test_idempotent_load_failure();
  printf("test_xpum_gpu_dyn passed\n");
  return 0;
}
