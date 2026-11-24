/* x86_uncore_perf - Intel IMC / HBM and AMD DF DRAM counters by direct perf_event_open.
 *
 * One device row per uncore PMU instance under the perf devices dir: `<prefix><N>` for indexed
 * PMUs (uncore_imc_<N> -> mbox<N> or mdev<N>, uncore_hbm_<N> -> hbm<N>; never a fixed box count)
 * or one row for an exact PMU name (amd_df -> df). Each attach reads the PMU's `type` and
 * `cpumask` and opens one counting fd per (event, cpumask CPU): an uncore cpumask names one CPU
 * per socket or die, and the row's value is the sum over them. An event resolves from sysfs
 * `events/<name>` first, else from its verified raw `event` / `umask` terms; every term is placed
 * through the PMU's `format/` files, multi-range fields (`config:0-7,32-35,59-60`) included.
 *
 * Reads are scaled (perf_util); lifetimes only grow (a scaled delta adds when > 0) and survive a
 * re-attach by row name. Counted keys are CAS or data-beat counts, never bytes, emitted modulo
 * 2^48 for their W=48 schema. Per row, pmu_enabled_time / pmu_running_time are the counter time
 * lifetimes (D6) over every fd of the row. A key whose counter could not be opened or read this
 * cycle emits its value sentinel, so a key summed over several fds never publishes a partial
 * sum. Never pinned or exclusive; opens happen at begin only. */
#ifndef X86_UNCORE_PERF_H
#define X86_UNCORE_PERF_H

#include <stdint.h>

#include "perf_util.h"

#define X86_UNCORE_CPU_DIR "/sys/devices/system/cpu"
#define X86_UNCORE_MAX_KEYS 8
#define X86_UNCORE_MAX_CPUS 16
#define X86_UNCORE_MAX_DEVS 64
#define X86_UNCORE_MAX_CLASSES 2

struct stats_type;

/** Schema entries every row of an x86_uncore_perf collector carries: its counter time
 * lifetimes in ns, summed over every fd of the row (no solo baseline for uncore PMUs). */
#define X86_UNCORE_PMU_TIME_KEYS                                                                   \
  X(pmu_enabled_time, "E,W=64,U=ns", ""), X(pmu_running_time, "E,W=64,U=ns", "")

/** One counted event: sysfs events/<sysfs_name> first, else the raw terms placed by
 * format/event and format/umask. Several events may add to one key. */
struct x86_uncore_event {
  int key; /* index into the profile's keys */
  const char *sysfs_name;
  uint64_t event;
  uint64_t umask;
  int row; /* rows-split classes: the row (DRAM channel) it feeds; events sorted by row */
};

/** One kind of uncore PMU. indexed: every `<pmu><N>` is the row `<dev><N>`; else the PMU named
 * exactly `<pmu>` is the row `<dev>`, or with n_rows > 0 its events split by row into the rows
 * `<dev>0` .. `<dev><n_rows - 1>` (AMD DF: one PMU, one row per DRAM channel). A row with no
 * event emits NOT_BUILT for the keys other rows feed. */
struct x86_uncore_class {
  const char *pmu;
  int indexed;
  const char *dev;
  const struct x86_uncore_event *events;
  int n_events;
  int n_rows;
};

/** What one collector reads. A key no event of a row's class feeds emits NOT_PRESENT on that
 * row, or NOT_BUILT when its bit is in not_built (no verified encoding in this monitor). */
struct x86_uncore_profile {
  const char *const *keys;
  int n_keys;
  unsigned int not_built;
  const struct x86_uncore_class *classes;
  int n_classes;
};

struct x86_uncore_counter {
  int fd;
  enum stats_sentinel open_status; /* why fd is -1 */
  struct perf_prev prev;
};

struct x86_uncore_dev {
  char name[24];
  const struct x86_uncore_class *cls;
  const struct x86_uncore_event *ev; /* this row's events: all of cls, or one row's slice */
  int n_ev;
  int n_cpus; /* CPUs of the PMU's cpumask; 0 when type or cpumask was unreadable */
  int cpus[X86_UNCORE_MAX_CPUS];
  struct x86_uncore_counter *ctr; /* [event * n_cpus + cpu] */
  unsigned long long life[X86_UNCORE_MAX_KEYS];
  enum stats_sentinel status[X86_UNCORE_MAX_KEYS]; /* from the last x86_uncore_perf_read */
  unsigned long long life_enabled_ns;
  unsigned long long life_running_ns;
  enum stats_sentinel time_status;
};

struct x86_uncore_perf {
  const struct x86_uncore_profile *profile;
  const char *devices_dir;
  const char *cpu_dir;  /* for the socket count */
  perf_open_fn open_fn; /* NULL: perf_open_cpu */
  struct x86_uncore_dev dev[X86_UNCORE_MAX_DEVS];
  int n;
  int n_open;                                       /* fds open after the last attach */
  int packages;                                     /* sockets under cpu_dir; 0 when unknown */
  unsigned int fail_logged[X86_UNCORE_MAX_CLASSES]; /* bit per event: open failure logged */
};

/** Static initializer for a collector's reader on the live sysfs. */
#define X86_UNCORE_PERF_INIT(prof)                                                                 \
  {.profile = (prof), .devices_dir = PERF_DEVICES_DIR, .cpu_dir = X86_UNCORE_CPU_DIR}

extern const struct x86_uncore_profile x86_uncore_profile_imc_skx;
extern const struct x86_uncore_profile x86_uncore_profile_imc_icx;
extern const struct x86_uncore_profile x86_uncore_profile_imc_spr; /* SPR and EMR: DDR + HBM */
extern const struct x86_uncore_profile x86_uncore_profile_imc_gnr; /* GNR and SRF: SCH0 CAS */
extern const struct x86_uncore_profile x86_uncore_profile_df_zen2;
extern const struct x86_uncore_profile x86_uncore_profile_df_zen3;
extern const struct x86_uncore_profile x86_uncore_profile_df_zen5; /* Zen4 and Zen5 */

/** CPUs of a sysfs cpulist ("0", "0,64", "0-3,8") into @p cpus. @return the count, -1 when
 * unparsable, empty or longer than @p cap. */
int x86_uncore_perf_parse_cpulist(const char *list, int *cpus, int cap);

/** Places @p value into @p attr through `<pmu_dir>/format/<term>`: the low value bits fill the
 * first range, the next bits the next range. `config`, `config1` and `config2` set that field
 * directly. @return 0, -1 when the format is missing or unparsable or @p value does not fit. */
int x86_uncore_perf_place_term(const char *pmu_dir, const char *term, uint64_t value,
                               struct perf_event_attr *attr);

/** Counting attr for @p ev on the PMU of @p type: events/<sysfs_name> when it parses and places,
 * else the raw terms. @return 0, -1 when neither resolves. */
int x86_uncore_perf_event_attr(const char *pmu_dir, uint32_t type,
                               const struct x86_uncore_event *ev, struct perf_event_attr *attr);

/** Distinct physical_package_id values under @p cpu_dir (cpu<N>/topology/); 0 when unknown. */
int x86_uncore_perf_count_packages(const char *cpu_dir);

/** (Re)discovers the profile's PMUs and opens every counter. Rows keep their lifetimes across a
 * re-attach by name; a row whose sysfs or opens fail stays, with sentinels. @return rows. */
int x86_uncore_perf_attach(struct x86_uncore_perf *u);

/** One scaled read of every open fd: grows lifetimes and sets each row's key statuses. */
void x86_uncore_perf_read(struct x86_uncore_perf *u);

/** Publishes every row on @p type: lifetimes, or the key's sentinel. */
void x86_uncore_perf_publish(const struct x86_uncore_perf *u, struct stats_type *type);

/** Closes every fd and forgets the rows. */
void x86_uncore_perf_close(struct x86_uncore_perf *u);

/** Collector begin: disables @p type when @p cpu_matches is 0 or no PMU of the profile exists,
 * else attaches. @return 0, -1 when disabled. */
int x86_uncore_perf_begin(struct stats_type *type, struct x86_uncore_perf *u, int cpu_matches);

/** Collector collect: read, then publish. */
void x86_uncore_perf_collect(struct stats_type *type, struct x86_uncore_perf *u);

#endif
