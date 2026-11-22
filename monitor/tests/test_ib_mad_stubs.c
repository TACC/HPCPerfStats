#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

void monitor_log_warn(const char *fmt, ...)
{
  (void)fmt;
}
