#ifndef CPU_COUNTER_METRICS_ARM_UTIL_H_
#define CPU_COUNTER_METRICS_ARM_UTIL_H_

/* Pure helpers for the `arm` host_cpu_hw backend (aarch64 / Grace): /proc/stat jiffy util and
 * the cpufreq clock. No DCGM: the CPU path reads only kernel sources (round 2, D9). */

#include "cpu_counter_metrics_arm_publish.h"

/* Root of the per-CPU sysfs tree (cpuN/cpufreq/...). Tests pass a fixture root. */
#define ARM_CPU_SYSFS_ROOT "/sys/devices/system/cpu"

struct arm_cpu_jifs {
  unsigned long long u, nice, sys, idle, iow, irq, sft, stl, gu, gn;
};

double arm_clamp_percent(double v);
/* Util percentages over one collect interval from two /proc/stat snapshots; leaves s unchanged
 * when the counters went backwards or no jiffies elapsed. */
void arm_cpu_sample_from_jiffy_diff(struct arm_cpu_sample *s, const struct arm_cpu_jifs *cur,
                                    const struct arm_cpu_jifs *prev);
/* Parse one "cpuN ..." line of /proc/stat. Returns N (>= 0) and fills *out, or -1. */
int arm_proc_stat_parse_cpu_line(const char *line, struct arm_cpu_jifs *out);

/* Open <root>/cpu<cpu>/cpufreq/scaling_cur_freq for pread; -1 when absent. cpuinfo_cur_freq is
 * never used (CPPC reports bogus values there on Grace, finding 17). */
int arm_cpu_freq_open(const char *root, int cpu);
/* kHz from an fd returned by arm_cpu_freq_open, 0 on error or fd < 0. */
double arm_cpu_freq_read_khz(int fd);
/* Nominal clock: base_frequency, cpuinfo_max_freq, scaling_max_freq, scaling_cur_freq, then the
 * same chain on cpu0; 0 when none is readable. Called once per CPU at begin. */
double arm_cpu_nominal_freq_khz(const char *root, int cpu);

#endif
