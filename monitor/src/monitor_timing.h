#ifndef MONITOR_TIMING_H
#define MONITOR_TIMING_H

double monitor_timing_normalize_period(double period);
double monitor_timing_next_boundary(double now, double period);

/* Fraction of a period after a collect start within which a boundary counts as
 * already served by that collect. */
#define MONITOR_TIMING_SERVED_FRACTION 0.25

/* Delay to arm a one-shot timer that measures it from `loop_now` (libev's
 * cached ev_now(), taken before the collect) so it fires on the next `period`
 * boundary after `wall_now` (fresh CLOCK_REALTIME). `served_at` is when the
 * previous collect started (non-finite or negative: none); a boundary less than
 * MONITOR_TIMING_SERVED_FRACTION of a period after it is skipped. A non-finite
 * `loop_now` falls back to `wall_now`. */
double monitor_timing_reanchor_wait(double loop_now, double wall_now, double served_at,
                                    double period);

/* Slow-tier boundary detection driven off the fast sample timer.
 *
 * monitor_collect_slow_slot() returns floor(now_sec / period), the index of the
 * slow-collection window that `now_sec` falls in (>= 0). A non-positive period
 * is normalized so every tick maps to a distinct slot (always-slow behavior).
 *
 * monitor_collect_should_run_slow() returns non-zero when the slow window has
 * advanced past `last_slow_slot` (use last_slow_slot < 0 to force the first
 * tick to run slow). */
long long monitor_collect_slow_slot(double now_sec, double period);
int monitor_collect_should_run_slow(double now_sec, long long last_slow_slot, double period);

#endif
