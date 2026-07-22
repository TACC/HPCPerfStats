/* Release (!DEBUG) logging policy helpers — first-fail latch and hourly deltas. */
#include "monitor_release_log.h"

#include <stddef.h>

int monitor_release_log_should_emit(int *latched, int first_only)
{
  if (latched == NULL)
    return 1;
  if (!first_only)
    return 1;
  if (*latched)
    return 0;
  *latched = 1;
  return 1;
}

void monitor_release_log_clear_latch(int *latched)
{
  if (latched != NULL)
    *latched = 0;
}

void monitor_release_log_failure_deltas(unsigned long prev_c, unsigned long prev_q,
                                        unsigned long prev_p, unsigned long cur_c,
                                        unsigned long cur_q, unsigned long cur_p,
                                        unsigned long *d_c, unsigned long *d_q, unsigned long *d_p)
{
  if (d_c != NULL)
    *d_c = cur_c - prev_c;
  if (d_q != NULL)
    *d_q = cur_q - prev_q;
  if (d_p != NULL)
    *d_p = cur_p - prev_p;
}
