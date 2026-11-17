/* Shared direct perf_event helpers for the aarch64 readers (arm_core_perf,
 * arm_imc SCF and dmc/imc). Portable C: builds and unit-tests on every host.
 *
 * Every counter is opened with PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING
 * and read as {value, time_enabled, time_running}, so kernel multiplexing (Grace: nvidia-powerd
 * holds most core PMCs) is scaled like `perf stat`. Never pinned or exclusive. */
#ifndef PERF_UTIL_H
#define PERF_UTIL_H

#include <stdint.h>
#include <linux/perf_event.h>

#include "stats_sentinel.h"

#define PERF_DEVICES_DIR "/sys/bus/event_source/devices"

/** Opens one counting event on @p cpu (pid -1); returns an fd or -1. Injectable for tests. */
typedef int (*perf_open_fn)(const struct perf_event_attr *attr, int cpu);

/** Previous {value, enabled, running} of one counter; zero-initialise before the first read. */
struct perf_prev {
  uint64_t value;
  uint64_t enabled;
  uint64_t running;
  int seeded;
};

/** perf_event_open(attr, -1, cpu, -1, CLOEXEC); returns an fd or -1 with errno set. */
int perf_open_cpu(const struct perf_event_attr *attr, int cpu);

/** Counting attr for a raw event code on a PMU type (scaled read format, not pinned). */
void perf_attr_raw(struct perf_event_attr *attr, uint32_t type, uint64_t config);

/** PMU type from `<pmu_dir>/type` (per boot; never hard-code). @return 0, or -1 when unreadable. */
int perf_pmu_type(const char *pmu_dir, uint32_t *type);

/**
 * Counting attr from sysfs: `<pmu_dir>/type` and `<pmu_dir>/events/<event>` (for example
 * "event=0x1a5"), each term placed by `<pmu_dir>/format/<term>`.
 * @return 0 on success, -1 when a file is missing or unparsable.
 */
int perf_attr_from_sysfs(const char *pmu_dir, const char *event, struct perf_event_attr *attr);

/** First CPU of a sysfs cpulist such as "72" or "0-3,8"; -1 when missing or unparsable. */
int perf_cpumask_first(const char *path);

/** Time deltas of one sample in ns (D6 sharing keys): how long the event was enabled and how
 * long it actually ran on a counter. Both 0 when the sample seeded or re-seeded. */
struct perf_times {
  uint64_t enabled;
  uint64_t running;
};

/** Value sentinel for a failed perf_event_open errno: EACCES / EPERM PERMISSION_DENIED, EBUSY
 * PMU_BUSY, ENOENT / ENODEV / EINVAL / EOPNOTSUPP NOT_PRESENT, anything else READ_FAILED. */
enum stats_sentinel perf_open_sentinel(int err);

/**
 * Scaled delta from one {value, enabled, running} sample:
 * delta = dvalue x denabled / drunning. The first sample seeds and gives 0; drunning == 0
 * gives 0; a value (or time) going down re-seeds without adding. @p times_out (may be NULL)
 * receives {denabled, drunning}, also when drunning == 0 (enabled but never scheduled).
 * @return what the interval measured: STATS_SENTINEL_OK (seed, idle or scaled, partial
 * multiplexing included), STATS_SENTINEL_NOT_SCHEDULED (enabled but never on a counter, so the
 * activity is unmeasured), STATS_SENTINEL_COUNTER_RESET (re-seed: the interval is lost), or
 * STATS_SENTINEL_READ_FAILED for invalid arguments.
 */
enum stats_sentinel perf_scale_sample(struct perf_prev *prev, const uint64_t sample[3],
                                      double *delta_out, struct perf_times *times_out);

/** Reads {value, enabled, running} from @p fd and applies perf_scale_sample; @p status_out (may
 * be NULL) gets its status, or STATS_SENTINEL_READ_FAILED on a failed read.
 * @return 0 on success, -1 on a short or failed read (*delta_out = 0, times 0, prev unchanged,
 * so the next good read spans the gap). */
int perf_read_scaled(int fd, struct perf_prev *prev, double *delta_out,
                     struct perf_times *times_out, enum stats_sentinel *status_out);

#endif
