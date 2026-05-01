#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "metric_profiler.h"

int main(void)
{
  FILE *f = tmpfile();
  char buf[4096];
  size_t n;

  if (f == NULL)
    return 1;

  for (int i = 0; i < 130; i++) {
    metric_profiler_cycle_begin();
    metric_profiler_collect_begin("cpu");
    metric_profiler_record_metric("cpu", "cpu0", "cycles", 1000ULL, 250ULL);
    metric_profiler_collect_end("cpu");
    metric_profiler_cycle_end(f);
  }

  fflush(f);
  rewind(f);
  n = fread(buf, 1, sizeof(buf) - 1, f);
  buf[n] = '\0';
  fclose(f);

#ifdef MONITOR_METRIC_PROFILER
  if (strstr(buf, "metric-profiler:") == NULL)
    return 2;
  if (strstr(buf, "wait_ns=") == NULL)
    return 3;
#else
  if (n != 0)
    return 4;
#endif

  return 0;
}
