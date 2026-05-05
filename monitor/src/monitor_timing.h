#ifndef MONITOR_TIMING_H
#define MONITOR_TIMING_H

double monitor_timing_normalize_period(double period);
double monitor_timing_next_boundary(double now, double period);
double monitor_timing_seconds_until_next_boundary(double now, double period);

#endif
