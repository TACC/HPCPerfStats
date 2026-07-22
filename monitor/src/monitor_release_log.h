#ifndef MONITOR_RELEASE_LOG_H
#define MONITOR_RELEASE_LOG_H

/*
 * Pure helpers for release (!DEBUG) logging policy: first-failure latches and
 * hourly counter deltas. Callers pass first_only=1 in release builds.
 */

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

#endif
