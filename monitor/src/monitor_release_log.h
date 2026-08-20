#ifndef MONITOR_RELEASE_LOG_H
#define MONITOR_RELEASE_LOG_H

/*
 * Pure helpers for release (!DEBUG) logging policy: first-failure latches,
 * named failure counters for hourly rollup, and stderr quieting around noisy
 * third-party probes (libibmad ibwarn). Callers pass first_only=1 in release.
 */

/*! Named recurring failure classes (ring resend, IB MAD, OPA MAD, nvidia zero-row). */
typedef enum {
  MONITOR_REL_FAIL_RING_RESEND = 0,
  MONITOR_REL_FAIL_IB_MAD,
  MONITOR_REL_FAIL_NVIDIA_ZERO,
  MONITOR_REL_FAIL_OPA_MAD,
  MONITOR_REL_FAIL_COUNT
} monitor_rel_fail_id;

/*! 1 in release (!DEBUG): first-fail latches; 0 in DEBUG: always emit. */
static inline int monitor_release_log_first_only(void)
{
#ifdef DEBUG
  return 0;
#else
  return 1;
#endif
}

/*! Return 1 if this event should be logged.
 *  When first_only is 0 (DEBUG), always returns 1 and leaves *latched unchanged.
 *  When first_only is 1, returns 1 only while *latched is 0, then sets *latched. */
int monitor_release_log_should_emit(int *latched, int first_only);

/*! Clear a first-failure latch (e.g. after a successful publish). */
void monitor_release_log_clear_latch(int *latched);

/*! Compute unsigned deltas for hourly rollup (handles wrap as unsigned subtract). */
void monitor_release_log_failure_deltas(unsigned long prev_c, unsigned long prev_q,
                                        unsigned long prev_p, unsigned long cur_c,
                                        unsigned long cur_q, unsigned long cur_p,
                                        unsigned long *d_c, unsigned long *d_q, unsigned long *d_p);

/*! Increment named failure counter; return 1 if caller should emit a log line.
 *  Uses an internal latch when first_only is non-zero. */
int monitor_release_fail_note(monitor_rel_fail_id id, int first_only);

/*! Clear first-fail latch for a named class (on recovery / success). */
void monitor_release_fail_clear(monitor_rel_fail_id id);

/*! Current total for a named failure class (0 if id out of range). */
unsigned long monitor_release_fail_count(monitor_rel_fail_id id);

/*! Snapshot ring / ib_mad / nvidia / opa_mad totals for hourly status.
 *  Any pointer may be NULL. */
void monitor_release_fail_get_counts(unsigned long *ring_resend, unsigned long *ib_mad,
                                     unsigned long *nvidia_zero, unsigned long *opa_mad);

/*! Reset counters and latches (unit tests only). */
void monitor_release_fail_reset_for_test(void);

/*! Redirect stderr to /dev/null (LIKWID-style). Sets *saved_fd / *null_fd to -1 on failure. */
void monitor_stderr_quiet_begin(int *saved_fd, int *null_fd);

/*! Restore stderr after monitor_stderr_quiet_begin. */
void monitor_stderr_quiet_end(int *saved_fd, int *null_fd);

#endif
