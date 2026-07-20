#ifndef MONITOR_TIMING_H
#define MONITOR_TIMING_H

double monitor_timing_normalize_period(double period);
double monitor_timing_next_boundary(double now, double period);
double monitor_timing_seconds_until_next_boundary(double now, double period);

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
