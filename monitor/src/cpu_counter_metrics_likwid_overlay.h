#ifndef CPU_COUNTER_METRICS_LIKWID_OVERLAY_H_
#define CPU_COUNTER_METRICS_LIKWID_OVERLAY_H_

struct stats;
struct stats_type;

int cpu_counter_metrics_likwid_overlay_begin(struct stats_type *type);
void cpu_counter_metrics_likwid_overlay_collect_cpu(struct stats *stats, int cpu);
void cpu_counter_metrics_likwid_overlay_cleanup(void);
int cpu_counter_metrics_likwid_overlay_ready(void);
int cpu_counter_metrics_likwid_overlay_prepare_collect(void);

#endif
