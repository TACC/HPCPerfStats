/* x86_uncore_perf - Intel IMC / HBM and AMD DF DRAM counters by direct perf (see the header).
 * x86 hardware builds only; portable C, so it unit-tests on every host. */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "monitor_log.h"
#include "stats.h"
#include "x86_uncore_perf.h"

#define X86_UNCORE_W48_MASK ((1ULL << 48) - 1ULL)
#define X86_UNCORE_MAX_INDEX 4096
#define X86_UNCORE_MAX_PACKAGES 64
#define X86_UNCORE_PATH_MAX 640

#define ARRAY_LEN(a) ((int)(sizeof(a) / sizeof((a)[0])))

/* Intel IMC CAS: sysfs cas_count_read / cas_count_write first, else the generation's
 * CAS_COUNT RD / WR raw codes. GNR / SRF count scheduler 0 (CAS_COUNT_SCH0, the SPR codes;
 * scheduler 1, event 0x06, is not counted). SPR Max uncore_hbm has no sysfs names; its CAS
 * counts 32-byte accesses. */
static const char *const imc_keys[] = {"dram_cas_reads", "dram_cas_writes"};
static const char *const skx_keys[] = {"dram_cas_reads", "dram_cas_writes", "dram_act_count",
                                       "dram_pre_count_miss"};
static const char *const spr_keys[] = {"dram_cas_reads", "dram_cas_writes", "hbm_cas_reads",
                                       "hbm_cas_writes"};

static const struct x86_uncore_event skx_cas[] = {{0, "cas_count_read", 0x04, 0x03, 0},
                                                  {1, "cas_count_write", 0x04, 0x0c, 0}};
static const struct x86_uncore_event icx_cas[] = {{0, "cas_count_read", 0x04, 0x0f, 0},
                                                  {1, "cas_count_write", 0x04, 0x30, 0}};
static const struct x86_uncore_event spr_cas[] = {{0, "cas_count_read", 0x05, 0xcf, 0},
                                                  {1, "cas_count_write", 0x05, 0xf0, 0}};
static const struct x86_uncore_event spr_hbm_cas[] = {{2, "cas_count_read", 0x05, 0xcf, 0},
                                                      {3, "cas_count_write", 0x05, 0xf0, 0}};

static const struct x86_uncore_class skx_classes[] = {
    {"uncore_imc_", 1, "mbox", skx_cas, ARRAY_LEN(skx_cas), 0}};
/* ICX keeps its historical mdev<N> row names. */
static const struct x86_uncore_class icx_classes[] = {
    {"uncore_imc_", 1, "mdev", icx_cas, ARRAY_LEN(icx_cas), 0}};
static const struct x86_uncore_class spr_classes[] = {
    {"uncore_imc_", 1, "mbox", spr_cas, ARRAY_LEN(spr_cas), 0},
    {"uncore_hbm_", 1, "hbm", spr_hbm_cas, ARRAY_LEN(spr_hbm_cas), 0}};
static const struct x86_uncore_class gnr_classes[] = {
    {"uncore_imc_", 1, "mbox", spr_cas, ARRAY_LEN(spr_cas), 0}};

const struct x86_uncore_profile x86_uncore_profile_imc_skx = {
    skx_keys, ARRAY_LEN(skx_keys), (1U << 2) | (1U << 3), skx_classes, ARRAY_LEN(skx_classes)};
const struct x86_uncore_profile x86_uncore_profile_imc_icx = {imc_keys, ARRAY_LEN(imc_keys), 0,
                                                              icx_classes, ARRAY_LEN(icx_classes)};
const struct x86_uncore_profile x86_uncore_profile_imc_spr = {spr_keys, ARRAY_LEN(spr_keys), 0,
                                                              spr_classes, ARRAY_LEN(spr_classes)};
const struct x86_uncore_profile x86_uncore_profile_imc_gnr = {imc_keys, ARRAY_LEN(imc_keys), 0,
                                                              gnr_classes, ARRAY_LEN(gnr_classes)};

/* AMD DF DRAM data beats (64 bytes each), one row per DRAM channel, summed over the amd_df
 * cpumask CPUs. Zen2 / Zen3: event 0x07 + 0x40 x N, umask 0x38 (perf
 * dram_channel_data_controller_N), one combined read + write count (dram_data_beats); Milan
 * channels 0-7 verified, Rome channels 0-1 only. Zen4 / Zen5: read beats event 0x1f + 0x40 x N
 * umask 0xffe and write beats umask 0xfff (dram_read_beats / dram_write_beats), one profile for
 * Genoa and Turin. Turin channels 0-11 verified (c573-002: the 12 channels sum to 0.986 of a read
 * load's bytes); Genoa shares the encoding and its 12 DDR5 channels. */
static const char *const df_keys[] = {"dram_data_beats", "dram_read_beats", "dram_write_beats"};

static const struct x86_uncore_event df_zen3_beats[] = {
    {0, "dram_channel_data_controller_0", 0x07, 0x38, 0},
    {0, "dram_channel_data_controller_1", 0x47, 0x38, 1},
    {0, "dram_channel_data_controller_2", 0x87, 0x38, 2},
    {0, "dram_channel_data_controller_3", 0xc7, 0x38, 3},
    {0, "dram_channel_data_controller_4", 0x107, 0x38, 4},
    {0, "dram_channel_data_controller_5", 0x147, 0x38, 5},
    {0, "dram_channel_data_controller_6", 0x187, 0x38, 6},
    {0, "dram_channel_data_controller_7", 0x1c7, 0x38, 7}};
static const struct x86_uncore_event df_zen5_beats[] = {
    {1, "local_or_remote_socket_read_data_beats_dram_0", 0x1f, 0xffe, 0},
    {2, "local_or_remote_socket_write_data_beats_dram_0", 0x1f, 0xfff, 0},
    {1, "local_or_remote_socket_read_data_beats_dram_1", 0x5f, 0xffe, 1},
    {2, "local_or_remote_socket_write_data_beats_dram_1", 0x5f, 0xfff, 1},
    {1, "local_or_remote_socket_read_data_beats_dram_2", 0x9f, 0xffe, 2},
    {2, "local_or_remote_socket_write_data_beats_dram_2", 0x9f, 0xfff, 2},
    {1, "local_or_remote_socket_read_data_beats_dram_3", 0xdf, 0xffe, 3},
    {2, "local_or_remote_socket_write_data_beats_dram_3", 0xdf, 0xfff, 3},
    {1, "local_or_remote_socket_read_data_beats_dram_4", 0x11f, 0xffe, 4},
    {2, "local_or_remote_socket_write_data_beats_dram_4", 0x11f, 0xfff, 4},
    {1, "local_or_remote_socket_read_data_beats_dram_5", 0x15f, 0xffe, 5},
    {2, "local_or_remote_socket_write_data_beats_dram_5", 0x15f, 0xfff, 5},
    {1, "local_or_remote_socket_read_data_beats_dram_6", 0x19f, 0xffe, 6},
    {2, "local_or_remote_socket_write_data_beats_dram_6", 0x19f, 0xfff, 6},
    {1, "local_or_remote_socket_read_data_beats_dram_7", 0x1df, 0xffe, 7},
    {2, "local_or_remote_socket_write_data_beats_dram_7", 0x1df, 0xfff, 7},
    {1, "local_or_remote_socket_read_data_beats_dram_8", 0x21f, 0xffe, 8},
    {2, "local_or_remote_socket_write_data_beats_dram_8", 0x21f, 0xfff, 8},
    {1, "local_or_remote_socket_read_data_beats_dram_9", 0x25f, 0xffe, 9},
    {2, "local_or_remote_socket_write_data_beats_dram_9", 0x25f, 0xfff, 9},
    {1, "local_or_remote_socket_read_data_beats_dram_10", 0x29f, 0xffe, 10},
    {2, "local_or_remote_socket_write_data_beats_dram_10", 0x29f, 0xfff, 10},
    {1, "local_or_remote_socket_read_data_beats_dram_11", 0x2df, 0xffe, 11},
    {2, "local_or_remote_socket_write_data_beats_dram_11", 0x2df, 0xfff, 11}};

static const struct x86_uncore_class df_zen2_classes[] = {
    {"amd_df", 0, "chan", df_zen3_beats, 2, 8}};
static const struct x86_uncore_class df_zen3_classes[] = {
    {"amd_df", 0, "chan", df_zen3_beats, ARRAY_LEN(df_zen3_beats), 8}};
static const struct x86_uncore_class df_zen5_classes[] = {
    {"amd_df", 0, "chan", df_zen5_beats, ARRAY_LEN(df_zen5_beats), 12}};

const struct x86_uncore_profile x86_uncore_profile_df_zen2 = {
    df_keys, ARRAY_LEN(df_keys), 0, df_zen2_classes, ARRAY_LEN(df_zen2_classes)};
const struct x86_uncore_profile x86_uncore_profile_df_zen3 = {
    df_keys, ARRAY_LEN(df_keys), 0, df_zen3_classes, ARRAY_LEN(df_zen3_classes)};
const struct x86_uncore_profile x86_uncore_profile_df_zen5 = {
    df_keys, ARRAY_LEN(df_keys), 0, df_zen5_classes, ARRAY_LEN(df_zen5_classes)};

/* Reads a small sysfs file into @p buf (NUL-terminated); -1 when missing or empty. */
static int read_small_file(const char *path, char *buf, size_t cap)
{
  ssize_t n = -1;
  int fd = open(path, O_RDONLY | O_CLOEXEC);

  if (fd >= 0) {
    n = read(fd, buf, cap - 1);
    close(fd);
  }
  if (n <= 0)
    return -1;
  buf[n] = '\0';
  return 0;
}

int x86_uncore_perf_parse_cpulist(const char *list, int *cpus, int cap)
{
  const char *p = list;
  int n = 0;

  if (list == NULL || cpus == NULL)
    return -1;
  while (*p != '\0' && *p != '\n') {
    char *end = NULL;
    long lo = strtol(p, &end, 10), hi;

    if (end == p || lo < 0)
      return -1;
    hi = lo;
    if (*end == '-') {
      p = end + 1;
      hi = strtol(p, &end, 10);
      if (end == p || hi < lo)
        return -1;
    }
    for (; lo <= hi; lo++) {
      if (n >= cap)
        return -1;
      cpus[n++] = (int)lo;
    }
    p = (*end == ',') ? end + 1 : end;
    if (*end != ',' && *end != '\0' && *end != '\n')
      return -1;
  }
  return n > 0 ? n : -1;
}

/* attr field a format or term names ("config", "config1", "config2"); NULL for others. */
static __u64 *attr_field(struct perf_event_attr *attr, const char *name)
{
  if (strcmp(name, "config") == 0)
    return &attr->config;
  if (strcmp(name, "config1") == 0)
    return &attr->config1;
  if (strcmp(name, "config2") == 0)
    return &attr->config2;
  return NULL;
}

/* Spreads @p value over the bit ranges of @p ranges ("0-7,32-35,59-60"). -1 when unparsable
 * or @p value has bits beyond the ranges' total width. */
static int place_ranges(__u64 *field, const char *ranges, uint64_t value)
{
  const char *p = ranges;

  for (;;) {
    char *end = NULL;
    long lo = strtol(p, &end, 10), hi = lo;
    int width;
    uint64_t mask;

    if (end == p || lo < 0)
      return -1;
    if (*end == '-')
      hi = strtol(end + 1, &end, 10);
    if (hi < lo || hi > 63)
      return -1;
    width = (int)(hi - lo + 1);
    mask = (width >= 64) ? ~0ULL : ((1ULL << width) - 1ULL);
    *field = (*field & ~(mask << lo)) | ((value & mask) << lo);
    value = (width >= 64) ? 0 : value >> width;
    if (*end != ',')
      break;
    p = end + 1;
  }
  return value == 0 ? 0 : -1;
}

int x86_uncore_perf_place_term(const char *pmu_dir, const char *term, uint64_t value,
                               struct perf_event_attr *attr)
{
  char path[X86_UNCORE_PATH_MAX];
  char fmt[128];
  char *colon;
  __u64 *field;

  if (pmu_dir == NULL || term == NULL || attr == NULL)
    return -1;
  field = attr_field(attr, term);
  if (field != NULL) {
    *field = value;
    return 0;
  }
  if (snprintf(path, sizeof(path), "%s/format/%s", pmu_dir, term) >= (int)sizeof(path) ||
      read_small_file(path, fmt, sizeof(fmt)) != 0 || (colon = strchr(fmt, ':')) == NULL)
    return -1;
  *colon = '\0';
  field = attr_field(attr, fmt);
  return field != NULL ? place_ranges(field, colon + 1, value) : -1;
}

/* attr from events/<name> ("event=0x05,umask=0xcf"; a bare term means 1). */
static int attr_from_sysfs_event(const char *pmu_dir, uint32_t type, const char *name,
                                 struct perf_event_attr *attr)
{
  char path[X86_UNCORE_PATH_MAX];
  char buf[256];
  char *tok, *save = NULL;
  int n_terms = 0;

  perf_attr_raw(attr, type, 0);
  if (snprintf(path, sizeof(path), "%s/events/%s", pmu_dir, name) >= (int)sizeof(path) ||
      read_small_file(path, buf, sizeof(buf)) != 0)
    return -1;
  for (tok = strtok_r(buf, ",\n\r\t ", &save); tok != NULL;
       tok = strtok_r(NULL, ",\n\r\t ", &save)) {
    char *eq = strchr(tok, '=');
    char *end = NULL;
    uint64_t v = 1;

    if (eq != NULL) {
      *eq = '\0';
      v = strtoull(eq + 1, &end, 0);
      if (end == eq + 1)
        return -1;
    }
    if (x86_uncore_perf_place_term(pmu_dir, tok, v, attr) != 0)
      return -1;
    n_terms++;
  }
  return n_terms > 0 ? 0 : -1;
}

int x86_uncore_perf_event_attr(const char *pmu_dir, uint32_t type,
                               const struct x86_uncore_event *ev, struct perf_event_attr *attr)
{
  if (pmu_dir == NULL || ev == NULL || attr == NULL)
    return -1;
  if (ev->sysfs_name != NULL && attr_from_sysfs_event(pmu_dir, type, ev->sysfs_name, attr) == 0)
    return 0;
  perf_attr_raw(attr, type, 0);
  return (x86_uncore_perf_place_term(pmu_dir, "event", ev->event, attr) == 0 &&
          x86_uncore_perf_place_term(pmu_dir, "umask", ev->umask, attr) == 0)
             ? 0
             : -1;
}

/* <N> of "<prefix><N>" (digits only), or -1. */
static int pmu_index(const char *name, const char *prefix)
{
  size_t plen = strlen(prefix);
  char *end = NULL;
  long idx;

  if (strncmp(name, prefix, plen) != 0 || name[plen] < '0' || name[plen] > '9')
    return -1;
  idx = strtol(name + plen, &end, 10);
  return (*end == '\0' && idx < X86_UNCORE_MAX_INDEX) ? (int)idx : -1;
}

int x86_uncore_perf_count_packages(const char *cpu_dir)
{
  unsigned long long seen = 0;
  int n = 0, bad = 0;
  struct dirent *de;
  DIR *d = (cpu_dir != NULL) ? opendir(cpu_dir) : NULL;

  if (d == NULL)
    return 0;
  while ((de = readdir(d)) != NULL && !bad) {
    char path[X86_UNCORE_PATH_MAX];
    char buf[32];
    long id;

    if (pmu_index(de->d_name, "cpu") < 0)
      continue;
    snprintf(path, sizeof(path), "%s/%s/topology/physical_package_id", cpu_dir, de->d_name);
    if (read_small_file(path, buf, sizeof(buf)) != 0)
      continue; /* offline CPUs have no topology */
    id = strtol(buf, NULL, 10);
    bad = (id < 0 || id >= X86_UNCORE_MAX_PACKAGES);
    if (!bad && !(seen & (1ULL << id))) {
      seen |= 1ULL << id;
      n++;
    }
  }
  closedir(d);
  return bad ? 0 : n;
}

void x86_uncore_perf_close(struct x86_uncore_perf *u)
{
  int i, j;

  if (u == NULL)
    return;
  for (i = 0; i < u->n; i++) {
    struct x86_uncore_dev *d = &u->dev[i];

    for (j = 0; d->ctr != NULL && j < d->n_ev * d->n_cpus; j++)
      if (d->ctr[j].fd >= 0)
        close(d->ctr[j].fd);
    free(d->ctr);
    d->ctr = NULL;
  }
  u->n = 0;
  u->n_open = 0;
}

/* Logs failed class event @p e of class @p ci once per attach. */
static void log_open_failure(struct x86_uncore_perf *u, int ci, int e, const char *pmu, int cpu,
                             const char *why)
{
  const struct x86_uncore_class *cls = &u->profile->classes[ci];
  const struct x86_uncore_event *ev = &cls->events[e];

  if (u->fail_logged[ci] & (1U << e))
    return;
  u->fail_logged[ci] |= 1U << e;
  monitor_log_warn("x86_uncore: %s event 0x%llx umask 0x%llx (%s) not opened on CPU %d (%s); "
                   "%s emits a sentinel\n",
                   pmu, (unsigned long long)ev->event, (unsigned long long)ev->umask,
                   ev->sysfs_name != NULL ? ev->sysfs_name : "raw", cpu, why,
                   u->profile->keys[ev->key]);
}

/* Opens row event @p e of @p d on every cpumask CPU. */
static void open_event(struct x86_uncore_perf *u, struct x86_uncore_dev *d, int ci, int e,
                       const char *pmu_dir, const char *pmu, uint32_t type)
{
  perf_open_fn open_fn = (u->open_fn != NULL) ? u->open_fn : perf_open_cpu;
  struct perf_event_attr attr;
  int resolved = x86_uncore_perf_event_attr(pmu_dir, type, &d->ev[e], &attr) == 0;
  int ce = (int)(d->ev - d->cls->events) + e, c;

  for (c = 0; c < d->n_cpus; c++) {
    struct x86_uncore_counter *ctr = &d->ctr[e * d->n_cpus + c];

    ctr->fd = -1;
    ctr->open_status = STATS_SENTINEL_NOT_PRESENT;
    if (!resolved) {
      log_open_failure(u, ci, ce, pmu, d->cpus[c], "no sysfs event or format for its terms");
      continue;
    }
    errno = 0;
    ctr->fd = open_fn(&attr, d->cpus[c]);
    if (ctr->fd >= 0) {
      u->n_open++;
      continue;
    }
    ctr->open_status = perf_open_sentinel(errno != 0 ? errno : EINVAL);
    log_open_failure(u, ci, ce, pmu, d->cpus[c], strerror(errno != 0 ? errno : EINVAL));
  }
}

/* Sets up row @p d for PMU instance @p index of class @p ci (@p row of a rows-split class, else
 * -1): type, cpumask, every event of the row. */
static void setup_dev(struct x86_uncore_perf *u, struct x86_uncore_dev *d, int ci, int index,
                      int row)
{
  const struct x86_uncore_class *cls = &u->profile->classes[ci];
  char pmu[64], pmu_dir[X86_UNCORE_PATH_MAX], path[X86_UNCORE_PATH_MAX + 16], mask[512];
  uint32_t type = 0;
  int e;

  memset(d, 0, sizeof(*d));
  d->cls = cls;
  d->ev = cls->events;
  d->n_ev = cls->n_events;
  if (row >= 0) {
    while (d->n_ev > 0 && d->ev->row < row) {
      d->ev++;
      d->n_ev--;
    }
    for (e = 0; e < d->n_ev && d->ev[e].row == row; e++)
      ;
    d->n_ev = e;
  }
  if (cls->indexed) {
    snprintf(pmu, sizeof(pmu), "%s%d", cls->pmu, index);
    snprintf(d->name, sizeof(d->name), "%s%d", cls->dev, index);
  } else {
    snprintf(pmu, sizeof(pmu), "%s", cls->pmu);
    if (row >= 0)
      snprintf(d->name, sizeof(d->name), "%s%d", cls->dev, row);
    else
      snprintf(d->name, sizeof(d->name), "%s", cls->dev);
  }
  if (d->n_ev == 0)
    return; /* no verified event for this row: its keys emit NOT_BUILT */
  snprintf(pmu_dir, sizeof(pmu_dir), "%s/%s", u->devices_dir, pmu);
  snprintf(path, sizeof(path), "%s/cpumask", pmu_dir);
  if (perf_pmu_type(pmu_dir, &type) == 0 && read_small_file(path, mask, sizeof(mask)) == 0)
    d->n_cpus = x86_uncore_perf_parse_cpulist(mask, d->cpus, X86_UNCORE_MAX_CPUS);
  if (d->n_cpus > 0)
    d->ctr = calloc((size_t)(d->n_ev * d->n_cpus), sizeof(*d->ctr));
  if (d->ctr == NULL) {
    d->n_cpus = 0;
    monitor_log_warn("x86_uncore: %s type or cpumask unreadable; row %s emits READ_FAILED\n", pmu,
                     d->name);
    return;
  }
  for (e = 0; e < d->n_ev; e++)
    open_event(u, d, ci, e, pmu_dir, pmu, type);
}

/* Instance indices of class @p cls under the devices dir, ascending. @return count. */
static int discover(const struct x86_uncore_perf *u, const struct x86_uncore_class *cls, int *idx,
                    int cap)
{
  struct dirent *de;
  DIR *d = opendir(u->devices_dir);
  int n = 0, i, j;

  if (d == NULL)
    return 0;
  while ((de = readdir(d)) != NULL && n < cap) {
    int k =
        cls->indexed ? pmu_index(de->d_name, cls->pmu) : (strcmp(de->d_name, cls->pmu) ? -1 : 0);

    if (k >= 0)
      idx[n++] = k;
  }
  closedir(d);
  for (i = 1; i < n; i++)
    for (j = i; j > 0 && idx[j - 1] > idx[j]; j--) {
      int t = idx[j];

      idx[j] = idx[j - 1];
      idx[j - 1] = t;
    }
  return n;
}

/* Rows whose cpumask names fewer CPUs than the node has sockets count those sockets only (an
 * el8 kernel counts amd_df on socket 0 whatever CPU is named); logged once per PMU kind. */
static void log_socket_coverage(const struct x86_uncore_perf *u)
{
  int i;
  const struct x86_uncore_class *logged = NULL;

  for (i = 0; i < u->n; i++) {
    const struct x86_uncore_dev *d = &u->dev[i];

    if (d->cls == logged || d->n_cpus == 0 || d->n_cpus >= u->packages)
      continue;
    logged = d->cls;
    monitor_log_warn("x86_uncore: %s%s cpumask names %d CPU(s) on a %d-socket node; its rows "
                     "count only those sockets\n",
                     d->cls->pmu, d->cls->indexed ? "<N>" : "", d->n_cpus, u->packages);
  }
}

struct saved_life {
  char name[24];
  unsigned long long life[X86_UNCORE_MAX_KEYS];
  unsigned long long enabled, running;
};

int x86_uncore_perf_attach(struct x86_uncore_perf *u)
{
  struct saved_life saved[X86_UNCORE_MAX_DEVS];
  int n_saved, ci, i, j;

  if (u == NULL || u->profile == NULL || u->devices_dir == NULL)
    return 0;
  for (n_saved = 0; n_saved < u->n; n_saved++) {
    memcpy(saved[n_saved].name, u->dev[n_saved].name, sizeof(saved[n_saved].name));
    memcpy(saved[n_saved].life, u->dev[n_saved].life, sizeof(saved[n_saved].life));
    saved[n_saved].enabled = u->dev[n_saved].life_enabled_ns;
    saved[n_saved].running = u->dev[n_saved].life_running_ns;
  }
  x86_uncore_perf_close(u);
  memset(u->fail_logged, 0, sizeof(u->fail_logged));
  u->packages = x86_uncore_perf_count_packages(u->cpu_dir);
  for (ci = 0; ci < u->profile->n_classes && ci < X86_UNCORE_MAX_CLASSES; ci++) {
    int idx[X86_UNCORE_MAX_DEVS];
    int n = discover(u, &u->profile->classes[ci], idx, X86_UNCORE_MAX_DEVS - u->n);

    const struct x86_uncore_class *cls = &u->profile->classes[ci];

    for (i = 0; i < n; i++)
      for (j = 0; j < (cls->n_rows > 0 ? cls->n_rows : 1) && u->n < X86_UNCORE_MAX_DEVS; j++)
        setup_dev(u, &u->dev[u->n++], ci, idx[i], cls->n_rows > 0 ? j : -1);
  }
  for (i = 0; i < u->n; i++)
    for (j = 0; j < n_saved; j++)
      if (strcmp(u->dev[i].name, saved[j].name) == 0) {
        memcpy(u->dev[i].life, saved[j].life, sizeof(u->dev[i].life));
        u->dev[i].life_enabled_ns = saved[j].enabled;
        u->dev[i].life_running_ns = saved[j].running;
      }
  log_socket_coverage(u);
  return u->n;
}

/* Open failures can never be read; a lost interval breaks the consumer's chain; a failed read is
 * bridged by the next good one. The key keeps the strongest. */
static int status_rank(enum stats_sentinel s)
{
  switch (s) {
  case STATS_SENTINEL_OK:
    return 0;
  case STATS_SENTINEL_NOT_SCHEDULED:
  case STATS_SENTINEL_COUNTER_RESET:
    return 2;
  case STATS_SENTINEL_NOT_PRESENT:
  case STATS_SENTINEL_PERMISSION_DENIED:
  case STATS_SENTINEL_PMU_BUSY:
    return 3;
  default:
    return 1;
  }
}

static void merge_status(enum stats_sentinel *dst, enum stats_sentinel s)
{
  if (status_rank(s) > status_rank(*dst))
    *dst = s;
}

static void read_dev(struct x86_uncore_dev *d)
{
  enum stats_sentinel open_err = STATS_SENTINEL_OK;
  int any_open = 0, read_failed = 0, e, c;

  for (e = 0; e < X86_UNCORE_MAX_KEYS; e++)
    d->status[e] = (d->n_cpus == 0) ? STATS_SENTINEL_READ_FAILED : STATS_SENTINEL_OK;
  d->time_status = d->n_ev == 0 ? STATS_SENTINEL_NOT_BUILT : STATS_SENTINEL_READ_FAILED;
  for (e = 0; e < d->n_ev && d->n_cpus > 0; e++) {
    enum stats_sentinel *ks = &d->status[d->ev[e].key];

    for (c = 0; c < d->n_cpus; c++) {
      struct x86_uncore_counter *ctr = &d->ctr[e * d->n_cpus + c];
      enum stats_sentinel st;
      struct perf_times t;
      double delta;

      if (ctr->fd < 0) {
        merge_status(ks, ctr->open_status);
        merge_status(&open_err, ctr->open_status);
        continue;
      }
      any_open = 1;
      read_failed |= perf_read_scaled(ctr->fd, &ctr->prev, &delta, &t, &st) != 0;
      if (delta > 0.0)
        d->life[d->ev[e].key] += (unsigned long long)(delta + 0.5);
      d->life_enabled_ns += t.enabled; /* times are 0 on a failed read */
      d->life_running_ns += t.running;
      merge_status(ks, st);
    }
  }
  if (d->n_cpus > 0)
    d->time_status =
        any_open ? (read_failed ? STATS_SENTINEL_READ_FAILED : STATS_SENTINEL_OK) : open_err;
}

void x86_uncore_perf_read(struct x86_uncore_perf *u)
{
  int i;

  for (i = 0; u != NULL && i < u->n; i++)
    read_dev(&u->dev[i]);
}

/* Bit per profile key that one of the @p n events @p ev feeds. */
static unsigned int fed_keys(const struct x86_uncore_event *ev, int n)
{
  unsigned int bits = 0;
  int e;

  for (e = 0; e < n; e++)
    bits |= 1U << ev[e].key;
  return bits;
}

static void publish_value(struct stats *s, const char *key, unsigned long long v,
                          enum stats_sentinel st)
{
  if (st == STATS_SENTINEL_OK)
    stats_set(s, key, v);
  else
    stats_set_missing(s, key, st);
}

void x86_uncore_perf_publish(const struct x86_uncore_perf *u, struct stats_type *type)
{
  int i, k;

  for (i = 0; u != NULL && type != NULL && i < u->n; i++) {
    const struct x86_uncore_dev *d = &u->dev[i];
    unsigned int fed = fed_keys(d->ev, d->n_ev);
    unsigned int not_built = u->profile->not_built | fed_keys(d->cls->events, d->cls->n_events);
    struct stats *s = get_current_stats(type, d->name);

    if (s == NULL)
      continue;
    for (k = 0; k < u->profile->n_keys; k++) {
      const char *key = u->profile->keys[k];

      if (!(fed & (1U << k)))
        stats_set_missing(s, key,
                          (not_built & (1U << k)) ? STATS_SENTINEL_NOT_BUILT
                                                  : STATS_SENTINEL_NOT_PRESENT);
      else
        publish_value(s, key, d->life[k] & X86_UNCORE_W48_MASK, d->status[k]);
    }
    publish_value(s, "pmu_enabled_time", d->life_enabled_ns, d->time_status);
    publish_value(s, "pmu_running_time", d->life_running_ns, d->time_status);
  }
}

int x86_uncore_perf_begin(struct stats_type *type, struct x86_uncore_perf *u, int cpu_matches)
{
  if (type == NULL || u == NULL)
    return -1;
  if (!cpu_matches) {
    x86_uncore_perf_close(u);
    monitor_log_info("%s: disabled (processor does not match)\n", type->st_name);
  } else if (x86_uncore_perf_attach(u) == 0) {
    monitor_log_error("%s: no uncore PMU of this processor under %s; disabling type\n",
                      type->st_name, u->devices_dir);
  } else {
    monitor_log_info("%s: %d row(s), %d counter fd(s) open via direct perf\n", type->st_name, u->n,
                     u->n_open);
    return 0;
  }
  type->st_enabled = 0;
  return -1;
}

void x86_uncore_perf_collect(struct stats_type *type, struct x86_uncore_perf *u)
{
  x86_uncore_perf_read(u);
  x86_uncore_perf_publish(u, type);
}
