/* host_opa MAD failure backoff — always compiled (usable without liboib_utils). */
#include <time.h>

#include "opa_mad_backoff.h"

#define OPA_MAD_FAIL_STREAK_MAX 8
#define OPA_MAD_BACKOFF_SEC 60

static unsigned long g_opa_mad_fail_streak;
static time_t g_opa_mad_skip_until;

void opa_mad_note_success(void)
{
  g_opa_mad_fail_streak = 0;
  g_opa_mad_skip_until = 0;
}

void opa_mad_note_failure(void)
{
  time_t now;

  g_opa_mad_fail_streak++;
  if (g_opa_mad_fail_streak < OPA_MAD_FAIL_STREAK_MAX)
    return;
  now = time(NULL);
  if (now > 0)
    g_opa_mad_skip_until = now + OPA_MAD_BACKOFF_SEC;
}

int opa_mad_collect_cycle_ok(void)
{
  time_t now;

  if (g_opa_mad_fail_streak < OPA_MAD_FAIL_STREAK_MAX)
    return 1;
  now = time(NULL);
  if (now <= 0 || g_opa_mad_skip_until <= 0 || now >= g_opa_mad_skip_until) {
    g_opa_mad_fail_streak = 0;
    g_opa_mad_skip_until = 0;
    return 1;
  }
  return 0;
}

void opa_mad_test_reset_backoff(void)
{
  g_opa_mad_fail_streak = 0;
  g_opa_mad_skip_until = 0;
}

void opa_mad_test_set_fail_streak(unsigned long n)
{
  g_opa_mad_fail_streak = n;
  if (n >= OPA_MAD_FAIL_STREAK_MAX) {
    time_t now = time(NULL);
    g_opa_mad_skip_until = (now > 0) ? now + OPA_MAD_BACKOFF_SEC : 1;
  }
}
