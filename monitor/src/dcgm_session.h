#ifndef DCGM_SESSION_H
#define DCGM_SESSION_H

#include "dcgm_structs.h"

/*
 * After dcgmInit(), obtain a host-engine handle: embedded v2/legacy, or loopback
 * nv-hostengine. Sets *use_disconnect when the session used dcgmConnect_v2
 * (teardown with dcgmDisconnect rather than dcgmStopEmbedded).
 */
dcgmReturn_t monitor_dcgm_attach_for_process(dcgmHandle_t *outh, int *use_disconnect);

#endif
