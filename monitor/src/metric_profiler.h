/* metric_profiler.h — optional per-collect timing profiler (MONITOR_METRIC_PROFILER). */
#ifndef METRIC_PROFILER_H
#define METRIC_PROFILER_H

#include <stdio.h>

#ifdef MONITOR_METRIC_PROFILER
void metric_profiler_cycle_begin(void);
void metric_profiler_cycle_end(FILE *out);
void metric_profiler_collect_begin(const char *type_name);
void metric_profiler_collect_end(const char *type_name);
void metric_profiler_record_metric(const char *type_name, const char *dev, const char *key,
                                   unsigned long long wall_ns, unsigned long long cpu_ns);
#else
static inline void metric_profiler_cycle_begin(void) {}
static inline void metric_profiler_cycle_end(FILE *out) { (void)out; }
static inline void metric_profiler_collect_begin(const char *type_name) { (void)type_name; }
static inline void metric_profiler_collect_end(const char *type_name) { (void)type_name; }
static inline void metric_profiler_record_metric(const char *type_name, const char *dev, const char *key,
                                                 unsigned long long wall_ns,
                                                 unsigned long long cpu_ns)
{
  (void)type_name;
  (void)dev;
  (void)key;
  (void)wall_ns;
  (void)cpu_ns;
}
#endif

#endif
