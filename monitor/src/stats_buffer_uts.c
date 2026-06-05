/* Process-wide uname(2) cache for stats_buffer header and sample lines. */
#include <stdio.h>
#include <string.h>
#include <sys/utsname.h>
#include "stats_buffer_uts.h"

static struct utsname cached_uts;
static int cached_uts_valid;

void stats_buffer_ensure_uts_cached(void)
{
  if (cached_uts_valid)
    return;
#ifdef STATS_BUFFER_TEST_UTS_HOOK
  memset(&cached_uts, 0, sizeof(cached_uts));
  snprintf(cached_uts.nodename, sizeof(cached_uts.nodename), "%s", "golden_host");
  snprintf(cached_uts.sysname, sizeof(cached_uts.sysname), "%s", "Linux");
  snprintf(cached_uts.machine, sizeof(cached_uts.machine), "%s", "aarch64");
  snprintf(cached_uts.release, sizeof(cached_uts.release), "%s", "6.0.0");
  snprintf(cached_uts.version, sizeof(cached_uts.version), "%s", "#1 SMP");
#else
  uname(&cached_uts);
#endif
  cached_uts_valid = 1;
}

void stats_buffer_uts_cache_reset(void)
{
  cached_uts_valid = 0;
}

const struct utsname *stats_buffer_cached_uts(void)
{
  stats_buffer_ensure_uts_cached();
  return &cached_uts;
}
