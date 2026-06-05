#include <stdarg.h>

#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

int pscanf(const char *path, const char *fmt, ...)
{
  (void) path;
  (void) fmt;
  return -1;
}

void monitor_log_warn(const char *fmt, ...)
{
  (void) fmt;
}
