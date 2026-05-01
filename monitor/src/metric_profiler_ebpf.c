#include "metric_profiler_ebpf.h"

#include <stdio.h>
#include <string.h>

static void read_io_counters(struct metric_profiler_attr_sample *sample)
{
  FILE *f;
  char key[64];
  unsigned long long value;

  f = fopen("/proc/self/io", "r");
  if (f == NULL)
    return;

  while (fscanf(f, "%63[^:]: %llu%*[\n]", key, &value) == 2) {
    if (strcmp(key, "read_bytes") == 0)
      sample->read_bytes = value;
    else if (strcmp(key, "write_bytes") == 0)
      sample->write_bytes = value;
    else if (strcmp(key, "syscr") == 0)
      sample->syscr = value;
    else if (strcmp(key, "syscw") == 0)
      sample->syscw = value;
  }

  fclose(f);
}

static void read_ctx_switches(struct metric_profiler_attr_sample *sample)
{
  FILE *f;
  char key[96];
  long value;

  f = fopen("/proc/self/status", "r");
  if (f == NULL)
    return;

  while (fscanf(f, "%95[^:]: %ld%*[\n]", key, &value) == 2) {
    if (strcmp(key, "voluntary_ctxt_switches") == 0)
      sample->nvcsw = value;
    else if (strcmp(key, "nonvoluntary_ctxt_switches") == 0)
      sample->nivcsw = value;
  }

  fclose(f);
}

void metric_profiler_attr_capture(struct metric_profiler_attr_sample *sample)
{
  memset(sample, 0, sizeof(*sample));
  read_io_counters(sample);
  read_ctx_switches(sample);
}

void metric_profiler_attr_delta(const struct metric_profiler_attr_sample *begin,
                                const struct metric_profiler_attr_sample *end,
                                struct metric_profiler_attr_sample *delta)
{
  memset(delta, 0, sizeof(*delta));
  delta->read_bytes = end->read_bytes - begin->read_bytes;
  delta->write_bytes = end->write_bytes - begin->write_bytes;
  delta->syscr = end->syscr - begin->syscr;
  delta->syscw = end->syscw - begin->syscw;
  delta->nvcsw = end->nvcsw - begin->nvcsw;
  delta->nivcsw = end->nivcsw - begin->nivcsw;
}

void metric_profiler_attr_fprint(FILE *out, const struct metric_profiler_attr_sample *delta)
{
  fprintf(out,
          " io_read_bytes=%llu io_write_bytes=%llu sys_read_calls=%llu sys_write_calls=%llu"
          " ctxsw_vol=%ld ctxsw_invol=%ld",
          delta->read_bytes, delta->write_bytes, delta->syscr, delta->syscw, delta->nvcsw,
          delta->nivcsw);
}
