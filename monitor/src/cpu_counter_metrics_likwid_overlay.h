#ifndef CPU_COUNTER_METRICS_LIKWID_OVERLAY_H_
#define CPU_COUNTER_METRICS_LIKWID_OVERLAY_H_

struct stats;
struct stats_type;

int cpu_counter_metrics_likwid_overlay_begin(struct stats_type *type);
void cpu_counter_metrics_likwid_overlay_collect_cpu(struct stats *stats, int cpu);
void cpu_counter_metrics_likwid_overlay_cleanup(void);
int cpu_counter_metrics_likwid_overlay_ready(void);
/* 1 when the overlay group also programs Grace SCF0/SCF1 (CMEM_RD_DATA / CMEM_WR_TOTAL_BYTES). */
int cpu_counter_metrics_likwid_overlay_has_scf(void);
int cpu_counter_metrics_likwid_overlay_prepare_collect(void);

#endif
