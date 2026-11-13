/* arm_perf_util — shared direct perf_event helpers for the aarch64 readers (arm_core_perf,
 * arm_imc SCF and dmc/imc). Portable C: builds and unit-tests on every host.
 *
 * Every counter is opened with PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING
 * and read as {value, time_enabled, time_running}, so kernel multiplexing (Grace: nvidia-powerd
 * holds most core PMCs) is scaled like `perf stat`. Never pinned or exclusive. */
#ifndef ARM_PERF_UTIL_H
#define ARM_PERF_UTIL_H

#include <stdint.h>
#include <linux/perf_event.h>

#define ARM_PERF_DEVICES_DIR "/sys/bus/event_source/devices"

/** Opens one counting event on @p cpu (pid -1); returns an fd or -1. Injectable for tests. */
typedef int (*arm_perf_open_fn)(const struct perf_event_attr *attr, int cpu);

/** Previous {value, enabled, running} of one counter; zero-initialise before the first read. */
struct arm_perf_prev {
  uint64_t value;
  uint64_t enabled;
  uint64_t running;
  int seeded;
};

/** perf_event_open(attr, -1, cpu, -1, CLOEXEC); returns an fd or -1 with errno set. */
int arm_perf_open_cpu(const struct perf_event_attr *attr, int cpu);

/** Counting attr for a raw event code on a PMU type (scaled read format, not pinned). */
void arm_perf_attr_raw(struct perf_event_attr *attr, uint32_t type, uint64_t config);

/** PMU type from `<pmu_dir>/type` (per boot; never hard-code). @return 0, or -1 when unreadable. */
int arm_perf_pmu_type(const char *pmu_dir, uint32_t *type);

/**
 * Counting attr from sysfs: `<pmu_dir>/type` and `<pmu_dir>/events/<event>` (for example
 * "event=0x1a5"), each term placed by `<pmu_dir>/format/<term>`.
 * @return 0 on success, -1 when a file is missing or unparsable.
 */
int arm_perf_attr_from_sysfs(const char *pmu_dir, const char *event, struct perf_event_attr *attr);

/** First CPU of a sysfs cpulist such as "72" or "0-3,8"; -1 when missing or unparsable. */
int arm_perf_cpumask_first(const char *path);

/**
 * Scaled delta from one {value, enabled, running} sample:
 * delta = dvalue x denabled / drunning. The first sample seeds and gives 0; drunning == 0
 * gives 0; a value (or time) going down re-seeds without adding.
 */
void arm_perf_scale_sample(struct arm_perf_prev *prev, const uint64_t sample[3], double *delta_out);

/** Reads {value, enabled, running} from @p fd and applies arm_perf_scale_sample.
 * @return 0 on success, -1 on a short or failed read (*delta_out = 0, prev unchanged). */
int arm_perf_read_scaled(int fd, struct arm_perf_prev *prev, double *delta_out);

#endif
