#ifndef CPU_COUNTER_METRICS_PAPI_H_
#define CPU_COUNTER_METRICS_PAPI_H_

#ifdef MONITOR_CPU_PAPI_FLOPS

struct stats;
struct stats_type;

int cpu_counter_metrics_papi_begin(struct stats_type *type);
void cpu_counter_metrics_papi_collect_cpu(struct stats *stats, int cpu);
void cpu_counter_metrics_papi_cleanup(void);
int cpu_counter_metrics_papi_ready(void);

#endif /* MONITOR_CPU_PAPI_FLOPS */

#endif
