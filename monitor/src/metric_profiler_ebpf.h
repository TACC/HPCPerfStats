#ifndef METRIC_PROFILER_EBPF_H
#define METRIC_PROFILER_EBPF_H

#include <stdio.h>

struct metric_profiler_attr_sample {
  unsigned long long read_bytes;
  unsigned long long write_bytes;
  unsigned long long syscr;
  unsigned long long syscw;
  long nvcsw;
  long nivcsw;
};

void metric_profiler_attr_capture(struct metric_profiler_attr_sample *sample);
void metric_profiler_attr_delta(const struct metric_profiler_attr_sample *begin,
                                const struct metric_profiler_attr_sample *end,
                                struct metric_profiler_attr_sample *delta);
void metric_profiler_attr_fprint(FILE *out, const struct metric_profiler_attr_sample *delta);

#endif
