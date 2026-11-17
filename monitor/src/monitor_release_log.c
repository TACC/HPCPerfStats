/* Release (!DEBUG) logging policy helpers: first-fail latch, named counters, stderr quiet. */
#include "monitor_release_log.h"

#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

#include "stats_sentinel.h"

static unsigned long g_rel_fail_counts[MONITOR_REL_FAIL_COUNT];
static int g_rel_fail_latched[MONITOR_REL_FAIL_COUNT];
/* Indexed by catalog code; [0] unused. */
static unsigned long g_rel_sentinel_counts[STATS_SENTINEL_COUNT + 1];
static unsigned long g_rel_sentinel_reported[STATS_SENTINEL_COUNT + 1];

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

int monitor_release_fail_note(monitor_rel_fail_id id, int first_only)
{
  if ((int)id < 0 || id >= MONITOR_REL_FAIL_COUNT)
    return 1;
  g_rel_fail_counts[id]++;
  return monitor_release_log_should_emit(&g_rel_fail_latched[id], first_only);
}

void monitor_release_fail_clear(monitor_rel_fail_id id)
{
  if ((int)id < 0 || id >= MONITOR_REL_FAIL_COUNT)
    return;
  monitor_release_log_clear_latch(&g_rel_fail_latched[id]);
}

unsigned long monitor_release_fail_count(monitor_rel_fail_id id)
{
  if ((int)id < 0 || id >= MONITOR_REL_FAIL_COUNT)
    return 0;
  return g_rel_fail_counts[id];
}

void monitor_release_fail_get_counts(unsigned long *ring_resend, unsigned long *ib_mad,
                                     unsigned long *nvidia_zero, unsigned long *opa_mad)
{
  if (ring_resend != NULL)
    *ring_resend = g_rel_fail_counts[MONITOR_REL_FAIL_RING_RESEND];
  if (ib_mad != NULL)
    *ib_mad = g_rel_fail_counts[MONITOR_REL_FAIL_IB_MAD];
  if (nvidia_zero != NULL)
    *nvidia_zero = g_rel_fail_counts[MONITOR_REL_FAIL_NVIDIA_ZERO];
  if (opa_mad != NULL)
    *opa_mad = g_rel_fail_counts[MONITOR_REL_FAIL_OPA_MAD];
}

void monitor_release_fail_reset_for_test(void)
{
  int i;

  for (i = 0; i < (int)MONITOR_REL_FAIL_COUNT; i++) {
    g_rel_fail_counts[i] = 0;
    g_rel_fail_latched[i] = 0;
  }
  for (i = 0; i <= STATS_SENTINEL_COUNT; i++) {
    g_rel_sentinel_counts[i] = 0;
    g_rel_sentinel_reported[i] = 0;
  }
}

void monitor_release_sentinel_note(unsigned int code)
{
  if (code >= 1 && code <= STATS_SENTINEL_COUNT)
    g_rel_sentinel_counts[code]++;
}

int monitor_release_sentinel_summary(char *buf, size_t len)
{
  size_t used = 0;
  int any = 0;

  if (buf == NULL || len == 0)
    return -1;
  used = (size_t)snprintf(buf, len, "sentinels:");
  for (unsigned int code = 1; code <= STATS_SENTINEL_COUNT; code++) {
    unsigned long delta = g_rel_sentinel_counts[code] - g_rel_sentinel_reported[code];

    g_rel_sentinel_reported[code] = g_rel_sentinel_counts[code];
    if (delta == 0)
      continue;
    any = 1;
    if (used < len)
      used += (size_t)snprintf(buf + used, len - used, " %s=%lu", stats_sentinel_slug(code), delta);
  }
  if (!any && used < len)
    used += (size_t)snprintf(buf + used, len - used, " none");
  return (int)used;
}

void monitor_stderr_quiet_begin(int *saved_fd, int *null_fd)
{
  if (saved_fd == NULL || null_fd == NULL)
    return;
  *saved_fd = -1;
  *null_fd = -1;
  *saved_fd = dup(STDERR_FILENO);
  if (*saved_fd < 0)
    return;
  *null_fd = open("/dev/null", O_WRONLY);
  if (*null_fd >= 0)
    (void)dup2(*null_fd, STDERR_FILENO);
}

void monitor_stderr_quiet_end(int *saved_fd, int *null_fd)
{
  if (saved_fd != NULL && *saved_fd >= 0) {
    (void)dup2(*saved_fd, STDERR_FILENO);
    close(*saved_fd);
    *saved_fd = -1;
  }
  if (null_fd != NULL && *null_fd >= 0) {
    close(*null_fd);
    *null_fd = -1;
  }
}
