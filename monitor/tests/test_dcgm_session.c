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
static unsigned int g_v2_deny_count;
static dcgmModuleId_t g_v2_deny_first;
static dcgmReturn_t g_v2_rc = DCGM_ST_OK;
static int g_v1_calls;

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
  g_v2_deny_count = params->blackListCount;
  g_v2_deny_first = params->blackList[0];
  if (g_v2_rc != DCGM_ST_OK)
    return g_v2_rc;
  params->dcgmHandle = g_fake_handle;
  return DCGM_ST_OK;
}

static dcgmReturn_t fake_dcgmStartEmbedded(dcgmOperationMode_t opMode, dcgmHandle_t *pDcgmHandle)
{
  (void)opMode;
  g_v1_calls++;
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

/* Plan monitor-cpu-seconds-reduction, Approach G: on c122-018 the embedded engine's NVSwitch
 * module used 28 core-s/h although no monitor field reads NVSwitch entities. */
static void test_embedded_start_denies_nvswitch_module(void)
{
  dcgmHandle_t h = (dcgmHandle_t)0;
  int disc = -1;

  monitor_dcgm_session_test_reset();
  g_fake_handle = (dcgmHandle_t)0xabcdu;
  g_v2_deny_count = 0;
  g_v2_deny_first = DcgmModuleIdCore;
  g_v2_rc = DCGM_ST_OK;
  g_v1_calls = 0;

  assert(monitor_dcgm_session_acquire(&h, &disc) == DCGM_ST_OK);
  assert(g_v2_deny_count == 1);
  assert(g_v2_deny_first == DcgmModuleIdNvSwitch);
  assert(g_v1_calls == 0);
  monitor_dcgm_session_release();
}

/* A DCGM that rejects the deny list still starts through the v1 entry point (today's
 * behavior: every module loaded), so the deny list can never cost the session. */
static void test_v2_failure_falls_back_to_v1(void)
{
  dcgmHandle_t h = (dcgmHandle_t)0;
  int disc = -1;

  monitor_dcgm_session_test_reset();
  g_fake_handle = (dcgmHandle_t)0xbeefu;
  g_v2_rc = DCGM_ST_BADPARAM;
  g_v1_calls = 0;

  assert(monitor_dcgm_session_acquire(&h, &disc) == DCGM_ST_OK);
  assert(h == g_fake_handle);
  assert(g_v1_calls == 1);
  g_v2_rc = DCGM_ST_OK;
  monitor_dcgm_session_release();
}

int main(void)
{
  install_hooks();
  test_double_acquire_single_init();
  test_release_shutdown_only_on_last();
  test_release_when_idle_is_noop();
  test_embedded_start_denies_nvswitch_module();
  test_v2_failure_falls_back_to_v1();
  dcgm_gpu_dyn_test_set_hooks(NULL);
  printf("test_dcgm_session passed\n");
  return 0;
}
