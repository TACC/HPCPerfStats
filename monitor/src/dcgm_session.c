#include <string.h>
#include "dcgm_agent.h"
#include "dcgm_structs.h"

static dcgmReturn_t monitor_dcgm_try_embedded(dcgmHandle_t *outh)
{
  dcgmReturn_t rc;
  dcgmStartEmbeddedV2Params_v1 ep;

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
  if (rc == DCGM_ST_OK && ep.dcgmHandle == (dcgmHandle_t)0)
    return dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, outh);
  if (rc == DCGM_ST_VER_MISMATCH || rc == DCGM_ST_NOT_SUPPORTED || rc == DCGM_ST_BADPARAM)
    return dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, outh);
  return dcgmStartEmbedded(DCGM_OPERATION_MODE_AUTO, outh);
}

static dcgmReturn_t monitor_dcgm_connect_loopback(dcgmHandle_t *outh)
{
  dcgmReturn_t rc;
  dcgmConnectV2Params_v2 cp;
  char localhost[] = "127.0.0.1";

  memset(&cp, 0, sizeof(cp));
  cp.version = dcgmConnectV2Params_version2;
  cp.persistAfterDisconnect = 0;
  cp.timeoutMs = 10000;
  cp.addressIsUnixSocket = 0;
  rc = dcgmConnect_v2(localhost, &cp, outh);
  return rc;
}

dcgmReturn_t monitor_dcgm_attach_for_process(dcgmHandle_t *outh, int *use_disconnect)
{
  dcgmReturn_t rc;

  *use_disconnect = 0;
  *outh = (dcgmHandle_t)0;

  rc = monitor_dcgm_try_embedded(outh);
  if (rc == DCGM_ST_OK && *outh != (dcgmHandle_t)0)
    return DCGM_ST_OK;

  (void) dcgmShutdown();
  rc = dcgmInit();
  if (rc != DCGM_ST_OK)
    return rc;

  rc = monitor_dcgm_connect_loopback(outh);
  if (rc == DCGM_ST_OK && *outh != (dcgmHandle_t)0) {
    *use_disconnect = 1;
    return DCGM_ST_OK;
  }

  (void) dcgmShutdown();
  return (rc != DCGM_ST_OK) ? rc : DCGM_ST_INIT_ERROR;
}
