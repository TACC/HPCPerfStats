#ifndef DCGM_SESSION_H
#define DCGM_SESSION_H

#include "dcgm_structs.h"

/*
 * Process-wide refcounted DCGM session (Grace CPU + nvidia_gpu share one attach).
 *
 * After the first successful acquire: dcgmInit + embedded or loopback host-engine.
 * Later acquires return the same handle. Only the last release may StopEmbedded /
 * Disconnect and dcgmShutdown — never tear down while another consumer still holds
 * a ref (avoids NVRM free_os_event races and repeated libdcgm lshw -json spawns).
 *
 * Sets *use_disconnect when the session used dcgmConnect_v2 (informational for
 * callers; teardown is owned by monitor_dcgm_session_release).
 */
dcgmReturn_t monitor_dcgm_session_acquire(dcgmHandle_t *outh, int *use_disconnect);
void monitor_dcgm_session_release(void);

/* Legacy name: same as monitor_dcgm_session_acquire. */
dcgmReturn_t monitor_dcgm_attach_for_process(dcgmHandle_t *outh, int *use_disconnect);

#ifdef DCGM_SESSION_TEST
/* Force-clear shared state between unit-test cases (not for production). */
void monitor_dcgm_session_test_reset(void);
int monitor_dcgm_session_test_refcount(void);
#endif

#endif
