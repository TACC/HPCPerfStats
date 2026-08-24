#ifndef CPU_COUNTER_METRICS_LIKWID_BEGIN_H_
#define CPU_COUNTER_METRICS_LIKWID_BEGIN_H_

/* Always visible: uncore/RAPL call this under HAVE_LIKWID even when the CPU
 * backend is DCGM (Horizon aarch64). DCGM builds provide a stub that returns 0.
 */
int cpu_counter_metrics_likwid_ready(void);

#ifndef MONITOR_CPU_BACKEND_DCGM

struct stats_type;
int likwid_backend_begin(struct stats_type *type);

#endif

#endif
