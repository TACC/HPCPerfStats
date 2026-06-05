/* Process-wide uname(2) cache for stats_buffer header and sample lines. */
#include <sys/utsname.h>
#include "stats_buffer_uts.h"

static struct utsname cached_uts;
static int cached_uts_valid;

void stats_buffer_ensure_uts_cached(void)
{
  if (cached_uts_valid)
    return;
  uname(&cached_uts);
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
