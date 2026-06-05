#ifndef CPU_COUNTER_METRICS_LIKWID_BEGIN_H_
#define CPU_COUNTER_METRICS_LIKWID_BEGIN_H_

#ifndef MONITOR_CPU_BACKEND_DCGM

struct stats_type;
int likwid_backend_begin(struct stats_type *type);
int cpu_counter_metrics_likwid_ready(void);

#endif

#endif
