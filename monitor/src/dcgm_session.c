/* dcgm_session — process-wide refcounted DCGM host-engine handle. */
#include <string.h>
#include "dcgm_gpu_api.h"
#include "dcgm_session.h"

static int g_dcgm_session_refcount;
static dcgmHandle_t g_dcgm_session_handle;
static int g_dcgm_session_use_disconnect;

static dcgmReturn_t monitor_dcgm_try_embedded(dcgmHandle_t *outh)
{
  dcgmReturn_t rc;
  dcgmStartEmbeddedV2Params_v1 ep;

  if (outh == NULL)
    return DCGM_ST_BADPARAM;
  memset(&ep, 0, sizeof(ep));
  ep.version = dcgmStartEmbeddedV2Params_version1;
  ep.opMode = DCGM_OPERATION_MODE_AUTO;
  ep.logFile = NULL;
  ep.severity = DcgmLoggingSeverityNone;

  rc = dcgmStartEmbedded_v2(&ep);
  if (rc == DCGM_ST_OK && ep.dcgmHandle != (dcgmHandle_t)0) {
    *outh = ep.dcgmHandle;
    return DCGM_ST_OK;
  }
  return dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, outh);
}

static dcgmReturn_t monitor_dcgm_connect_loopback(dcgmHandle_t *outh)
{
  dcgmReturn_t rc;
  dcgmConnectV2Params_v2 cp;
  char localhost[] = "127.0.0.1";

  if (outh == NULL)
    return DCGM_ST_BADPARAM;
  memset(&cp, 0, sizeof(cp));
  cp.version = dcgmConnectV2Params_version2;
  cp.persistAfterDisconnect = 0;
  cp.timeoutMs = 10000;
  cp.addressIsUnixSocket = 0;
  rc = dcgmConnect_v2(localhost, &cp, outh);
  return rc;
}

static dcgmReturn_t monitor_dcgm_session_attach_new(dcgmHandle_t *outh, int *use_disconnect)
{
  dcgmReturn_t rc;

  *use_disconnect = 0;
  *outh = (dcgmHandle_t)0;

  rc = monitor_dcgm_try_embedded(outh);
  if (rc == DCGM_ST_OK && *outh != (dcgmHandle_t)0)
    return DCGM_ST_OK;

  /*
   * Embedded failed. Re-init then try loopback hostengine. Safe only while no
   * other monitor consumer holds the shared session (refcount must be 0).
   */
  (void)dcgmShutdown();
  rc = dcgmInit();
  if (rc != DCGM_ST_OK)
    return rc;

  rc = monitor_dcgm_connect_loopback(outh);
  if (rc == DCGM_ST_OK && *outh != (dcgmHandle_t)0) {
    *use_disconnect = 1;
    return DCGM_ST_OK;
  }

  (void)dcgmShutdown();
  return (rc != DCGM_ST_OK) ? rc : DCGM_ST_INIT_ERROR;
}

dcgmReturn_t monitor_dcgm_session_acquire(dcgmHandle_t *outh, int *use_disconnect)
{
  dcgmReturn_t rc;

  if (outh == NULL || use_disconnect == NULL)
    return DCGM_ST_BADPARAM;

  if (g_dcgm_session_refcount > 0 && g_dcgm_session_handle != (dcgmHandle_t)0) {
    g_dcgm_session_refcount++;
    *outh = g_dcgm_session_handle;
    *use_disconnect = g_dcgm_session_use_disconnect;
    return DCGM_ST_OK;
  }

  rc = dcgmInit();
  if (rc != DCGM_ST_OK)
    return rc;

  rc = monitor_dcgm_session_attach_new(outh, use_disconnect);
  if (rc != DCGM_ST_OK || *outh == (dcgmHandle_t)0) {
    (void)dcgmShutdown();
    *outh = (dcgmHandle_t)0;
    *use_disconnect = 0;
    return (rc != DCGM_ST_OK) ? rc : DCGM_ST_INIT_ERROR;
  }

  g_dcgm_session_handle = *outh;
  g_dcgm_session_use_disconnect = *use_disconnect;
  g_dcgm_session_refcount = 1;
  return DCGM_ST_OK;
}

void monitor_dcgm_session_release(void)
{
  if (g_dcgm_session_refcount <= 0)
    return;

  g_dcgm_session_refcount--;
  if (g_dcgm_session_refcount > 0)
    return;

  if (g_dcgm_session_handle != (dcgmHandle_t)0) {
    if (g_dcgm_session_use_disconnect)
      (void)dcgmDisconnect(g_dcgm_session_handle);
    else
      (void)dcgmStopEmbedded(g_dcgm_session_handle);
  }
  (void)dcgmShutdown();
  g_dcgm_session_handle = (dcgmHandle_t)0;
  g_dcgm_session_use_disconnect = 0;
}

dcgmReturn_t monitor_dcgm_attach_for_process(dcgmHandle_t *outh, int *use_disconnect)
{
  return monitor_dcgm_session_acquire(outh, use_disconnect);
}

#ifdef DCGM_SESSION_TEST
void monitor_dcgm_session_test_reset(void)
{
  g_dcgm_session_refcount = 0;
  g_dcgm_session_handle = (dcgmHandle_t)0;
  g_dcgm_session_use_disconnect = 0;
}

int monitor_dcgm_session_test_refcount(void)
{
  return g_dcgm_session_refcount;
}
#endif
