#ifndef CPU_COUNTER_METRICS_LIKWID_BEGIN_H_
#define CPU_COUNTER_METRICS_LIKWID_BEGIN_H_

/* Exclusive LIKWID backend session ready (x86 uncore / RAPL begin require it). aarch64 builds
 * no LIKWID, so the `arm` backend never links this unit.
 */
int cpu_counter_metrics_likwid_ready(void);

#ifndef MONITOR_CPU_BACKEND_ARM

struct stats_type;
int likwid_backend_begin(struct stats_type *type);

#endif

#endif
