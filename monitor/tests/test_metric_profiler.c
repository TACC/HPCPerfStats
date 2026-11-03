#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "metric_profiler.h"

/* Run `cycles` profiled collect cycles that report into `f`; return bytes written to `f`. */
static size_t run_cycles(FILE *f, int cycles, char *buf, size_t cap)
{
  size_t n;

  for (int i = 0; i < cycles; i++) {
    metric_profiler_cycle_begin();
    metric_profiler_collect_begin("host_cpu");
    metric_profiler_record_metric("host_cpu", "cpu0", "cycles", 1000ULL, 250ULL);
    metric_profiler_collect_end("host_cpu");
    metric_profiler_cycle_end(f);
  }
  fflush(f);
  rewind(f);
  n = fread(buf, 1, cap - 1, f);
  buf[n] = '\0';
  return n;
}

/* Fewer cycles than the periodic interval print nothing until an explicit report
 * (the daemon calls metric_profiler_report at shutdown so short runs still get one). */
static int test_report_flushes_short_run(void)
{
  FILE *f = tmpfile();
  char buf[4096];
  size_t n;

  if (f == NULL)
    return 10;
  n = run_cycles(f, 3, buf, sizeof(buf));
  if (n != 0) {
    fclose(f);
    return 11;
  }
  metric_profiler_report(f);
  metric_profiler_report(NULL);
  fflush(f);
  rewind(f);
  n = fread(buf, 1, sizeof(buf) - 1, f);
  buf[n] = '\0';
  fclose(f);
#ifdef MONITOR_METRIC_PROFILER
  if (strstr(buf, "metric-profiler: cycles=3 ") == NULL)
    return 12;
  if (strstr(buf, "metric-profiler:type=host_cpu calls=3 ") == NULL)
    return 13;
#else
  if (n != 0)
    return 14;
#endif
  return 0;
}

static int test_periodic_report(void)
{
  FILE *f = tmpfile();
  char buf[4096];
  size_t n;

  if (f == NULL)
    return 1;
  n = run_cycles(f, 130, buf, sizeof(buf));
  fclose(f);
#ifdef MONITOR_METRIC_PROFILER
  if (n == 0 || strstr(buf, "metric-profiler:") == NULL)
    return 2;
  if (strstr(buf, "wait_ns=") == NULL)
    return 3;
#else
  if (n != 0)
    return 4;
#endif
  return 0;
}

int main(void)
{
  int rc = test_report_flushes_short_run();

  if (rc != 0)
    return rc;
  return test_periodic_report();
}
