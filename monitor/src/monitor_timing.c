#include <math.h>

#include "monitor_timing.h"

double monitor_timing_normalize_period(double period)
{
  if (!isfinite(period) || period <= 0.0)
    return 1.0;
  return period;
}

double monitor_timing_next_boundary(double now, double period)
{
  double normalized_period = monitor_timing_normalize_period(period);
  double slots;
  double next;

  if (!isfinite(now))
    now = 0.0;
  if (now < 0.0)
    now = 0.0;

  slots = floor(now / normalized_period);
  next = (slots + 1.0) * normalized_period;
  if (next <= now)
    next += normalized_period;
  return next;
}

double monitor_timing_seconds_until_next_boundary(double now, double period)
{
  double wait = monitor_timing_next_boundary(now, period) - now;
  if (wait < 0.000001)
    wait = 0.000001;
  return wait;
}
