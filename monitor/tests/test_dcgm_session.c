/* Unit tests for process-wide DCGM session refcount (no real libdcgm). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "dcgm_gpu_dyn.h"
#include "dcgm_session.h"

static int g_init_calls;
static int g_shutdown_calls;
static int g_stop_calls;
static int g_disconnect_calls;
static dcgmHandle_t g_fake_handle;

static dcgmReturn_t fake_dcgmInit(void)
{
  g_init_calls++;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_dcgmShutdown(void)
{
  g_shutdown_calls++;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_dcgmStartEmbedded_v2(dcgmStartEmbeddedV2Params_v1 *params)
{
  if (params == NULL)
    return DCGM_ST_BADPARAM;
  params->dcgmHandle = g_fake_handle;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_dcgmStartEmbedded(dcgmOperationMode_t opMode, dcgmHandle_t *pDcgmHandle)
{
  (void)opMode;
  if (pDcgmHandle == NULL)
    return DCGM_ST_BADPARAM;
  *pDcgmHandle = g_fake_handle;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_dcgmStopEmbedded(dcgmHandle_t pDcgmHandle)
{
  (void)pDcgmHandle;
  g_stop_calls++;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_dcgmDisconnect(dcgmHandle_t pDcgmHandle)
{
  (void)pDcgmHandle;
  g_disconnect_calls++;
  return DCGM_ST_OK;
}

static void install_hooks(void)
{
  struct dcgm_gpu_dyn_test_hooks hooks;

  memset(&hooks, 0, sizeof(hooks));
  hooks.dcgmInit = fake_dcgmInit;
  hooks.dcgmShutdown = fake_dcgmShutdown;
  hooks.dcgmStartEmbedded_v2 = fake_dcgmStartEmbedded_v2;
  hooks.dcgmStartEmbedded = fake_dcgmStartEmbedded;
  hooks.dcgmStopEmbedded = fake_dcgmStopEmbedded;
  hooks.dcgmDisconnect = fake_dcgmDisconnect;
  dcgm_gpu_dyn_test_set_hooks(&hooks);
}

static void test_double_acquire_single_init(void)
{
  dcgmHandle_t h1 = (dcgmHandle_t)0;
  dcgmHandle_t h2 = (dcgmHandle_t)0;
  int disc1 = -1;
  int disc2 = -1;

  monitor_dcgm_session_test_reset();
  g_init_calls = g_shutdown_calls = g_stop_calls = g_disconnect_calls = 0;
  g_fake_handle = (dcgmHandle_t)0xabcdu;

  assert(monitor_dcgm_session_acquire(&h1, &disc1) == DCGM_ST_OK);
  assert(h1 == g_fake_handle);
  assert(monitor_dcgm_session_test_refcount() == 1);
  assert(g_init_calls == 1);

  assert(monitor_dcgm_session_acquire(&h2, &disc2) == DCGM_ST_OK);
  assert(h2 == g_fake_handle);
  assert(disc2 == disc1);
  assert(monitor_dcgm_session_test_refcount() == 2);
  assert(g_init_calls == 1);
  assert(g_shutdown_calls == 0);
}

static void test_release_shutdown_only_on_last(void)
{
  dcgmHandle_t h = (dcgmHandle_t)0;
  int disc = 0;

  monitor_dcgm_session_test_reset();
  g_init_calls = g_shutdown_calls = g_stop_calls = g_disconnect_calls = 0;
  g_fake_handle = (dcgmHandle_t)0xabcdu;

  assert(monitor_dcgm_session_acquire(&h, &disc) == DCGM_ST_OK);
  assert(monitor_dcgm_session_acquire(&h, &disc) == DCGM_ST_OK);
  assert(monitor_dcgm_session_test_refcount() == 2);

  monitor_dcgm_session_release();
  assert(monitor_dcgm_session_test_refcount() == 1);
  assert(g_shutdown_calls == 0);
  assert(g_stop_calls == 0);

  monitor_dcgm_session_release();
  assert(monitor_dcgm_session_test_refcount() == 0);
  assert(g_stop_calls == 1);
  assert(g_shutdown_calls == 1);
  assert(g_disconnect_calls == 0);
}

static void test_release_when_idle_is_noop(void)
{
  monitor_dcgm_session_test_reset();
  g_shutdown_calls = g_stop_calls = 0;
  monitor_dcgm_session_release();
  assert(g_shutdown_calls == 0);
  assert(g_stop_calls == 0);
}

int main(void)
{
  install_hooks();
  test_double_acquire_single_init();
  test_release_shutdown_only_on_last();
  test_release_when_idle_is_noop();
  dcgm_gpu_dyn_test_set_hooks(NULL);
  printf("test_dcgm_session passed\n");
  return 0;
}
