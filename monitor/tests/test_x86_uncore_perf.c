/* x86_uncore_perf: PMU discovery, event encoding, cpumask CPUs, scaled lifetimes, sentinels and
 * sharing times on a fake sysfs tree with pipe-backed perf fds. The stats setters are recorded
 * by the stubs below. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "amd_x86_uncore_df.h"
#include "intel_icx_imc.h"
#include "intel_skx_imc.h"
#include "intel_spr_imc.h"
#include "stats.h"
#include "test_fake_perf.h"
#include "x86_uncore_perf.h"

#define MAX_RECS 256

struct rec {
  char dev[24];
  char key[32];
  unsigned long long val;
  int code; /* 0 = value set, else the sentinel */
};

static struct rec g_recs[MAX_RECS];
static int g_n_recs;
static struct stats *g_rows[X86_UNCORE_MAX_DEVS];
static int g_n_rows;

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  struct stats *s;
  int i;

  (void)type;
  for (i = 0; i < g_n_rows; i++)
    if (strcmp(g_rows[i]->s_dev, dev) == 0)
      return g_rows[i];
  assert(g_n_rows < X86_UNCORE_MAX_DEVS);
  s = calloc(1, sizeof(*s) + strlen(dev) + 1);
  assert(s != NULL);
  memcpy(s->s_dev, dev, strlen(dev) + 1);
  g_rows[g_n_rows++] = s;
  return s;
}

static struct rec *rec_slot(const char *dev, const char *key)
{
  int i;

  for (i = 0; i < g_n_recs; i++)
    if (strcmp(g_recs[i].dev, dev) == 0 && strcmp(g_recs[i].key, key) == 0)
      return &g_recs[i];
  assert(g_n_recs < MAX_RECS);
  snprintf(g_recs[g_n_recs].dev, sizeof(g_recs[0].dev), "%s", dev);
  snprintf(g_recs[g_n_recs].key, sizeof(g_recs[0].key), "%s", key);
  g_recs[g_n_recs].code = -1;
  return &g_recs[g_n_recs++];
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  struct rec *r = rec_slot(stats->s_dev, key);

  r->val = val;
  r->code = 0;
}

void(stats_set_missing)(struct stats *stats, const char *key, enum stats_sentinel code)
{
  rec_slot(stats->s_dev, key)->code = (int)code;
}

/* Published value of dev/key; asserts it was a value, not a sentinel. */
static unsigned long long val_of(const char *dev, const char *key)
{
  struct rec *r = rec_slot(dev, key);

  assert(r->code == 0);
  return r->val;
}

static int code_of(const char *dev, const char *key)
{
  return rec_slot(dev, key)->code;
}

static char g_root[512];
static char g_devs[600];
static char g_cpus[600];

static void tree_init(void)
{
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_devs, sizeof(g_devs), "%s/devices", g_root);
  snprintf(g_cpus, sizeof(g_cpus), "%s/cpu", g_root);
  fake_sysfs_mkdir(g_root, "devices");
  fake_sysfs_mkdir(g_root, "cpu");
}

static void tree_done(struct x86_uncore_perf *u)
{
  int i;

  x86_uncore_perf_close(u);
  fake_sysfs_rm(g_root);
  for (i = 0; i < g_n_rows; i++)
    free(g_rows[i]);
  g_n_rows = 0;
  g_n_recs = 0;
  fake_perf_reset();
}

static void add_file(const char *pmu, const char *sub, const char *content)
{
  char rel[256];

  snprintf(rel, sizeof(rel), "devices/%s/%s", pmu, sub);
  fake_sysfs_write(g_root, rel, content);
}

/* PMU devices/<name> with type, cpumask and the Intel IMC format (event 0-7, umask 8-15). */
static void add_pmu(const char *name, const char *type, const char *cpumask)
{
  add_file(name, "type", type);
  add_file(name, "cpumask", cpumask);
  add_file(name, "format/event", "config:0-7\n");
  add_file(name, "format/umask", "config:8-15\n");
}

static void add_cpu(int cpu, int package)
{
  char rel[128], val[16];

  snprintf(rel, sizeof(rel), "cpu/cpu%d/topology/physical_package_id", cpu);
  snprintf(val, sizeof(val), "%d\n", package);
  fake_sysfs_write(g_root, rel, val);
}

static struct x86_uncore_perf make_reader(const struct x86_uncore_profile *p)
{
  struct x86_uncore_perf u = X86_UNCORE_PERF_INIT(p);

  u.devices_dir = g_devs;
  u.cpu_dir = g_cpus;
  u.open_fn = fake_perf_open;
  return u;
}

static void read_and_publish(struct x86_uncore_perf *u)
{
  struct stats_type t;

  memset(&t, 0, sizeof(t));
  x86_uncore_perf_collect(&t, u);
}

static void test_parse_cpulist(void)
{
  int cpus[8];

  assert(x86_uncore_perf_parse_cpulist("0\n", cpus, 8) == 1 && cpus[0] == 0);
  assert(x86_uncore_perf_parse_cpulist("0,64\n", cpus, 8) == 2 && cpus[1] == 64);
  assert(x86_uncore_perf_parse_cpulist("0-3,8", cpus, 8) == 5 && cpus[3] == 3 && cpus[4] == 8);
  assert(x86_uncore_perf_parse_cpulist("", cpus, 8) == -1);
  assert(x86_uncore_perf_parse_cpulist("x", cpus, 8) == -1);
  assert(x86_uncore_perf_parse_cpulist("3-1", cpus, 8) == -1);
  assert(x86_uncore_perf_parse_cpulist("0;1", cpus, 8) == -1);
  assert(x86_uncore_perf_parse_cpulist("0-8", cpus, 8) == -1);
  printf("test_parse_cpulist passed\n");
}

static void test_place_term_multi_range(void)
{
  struct perf_event_attr a;
  char pmu_dir[700];

  tree_init();
  add_pmu("amd_df", "11", "0\n");
  add_file("amd_df", "format/event", "config:0-7,32-35,59-60\n");
  add_file("amd_df", "format/umask", "config:8-15,24-27\n");
  snprintf(pmu_dir, sizeof(pmu_dir), "%s/amd_df", g_devs);
  memset(&a, 0, sizeof(a));
  /* Zen4 12-bit umask: low byte at 8-15, high nibble at 24-27. */
  assert(x86_uncore_perf_place_term(pmu_dir, "umask", 0xffe, &a) == 0);
  assert(a.config == ((0xfeULL << 8) | (0xfULL << 24)));
  /* Zen3 event: bits 0-7, then 32-35, then 59-60. */
  a.config = 0;
  assert(x86_uncore_perf_place_term(pmu_dir, "event", 0x2507, &a) == 0);
  assert(a.config == (0x07ULL | (0x5ULL << 32) | (0x2ULL << 59)));
  assert(x86_uncore_perf_place_term(pmu_dir, "event", 0x4000, &a) == -1);
  /* The Zen3 umask is 8 bits wide: a Zen4 umask does not fit. */
  add_file("amd_df", "format/umask", "config:8-15\n");
  assert(x86_uncore_perf_place_term(pmu_dir, "umask", 0xffe, &a) == -1);
  assert(x86_uncore_perf_place_term(pmu_dir, "config1", 0x55, &a) == 0 && a.config1 == 0x55);
  assert(x86_uncore_perf_place_term(pmu_dir, "nosuch", 1, &a) == -1);
  add_file("amd_df", "format/edge", "period:18\n");
  assert(x86_uncore_perf_place_term(pmu_dir, "edge", 1, &a) == -1);
  fake_sysfs_rm(g_root);
  printf("test_place_term_multi_range passed\n");
}

static void test_discovery_by_prefix(void)
{
  struct x86_uncore_perf u;

  tree_init();
  add_pmu("uncore_imc_10", "30", "0\n");
  add_pmu("uncore_imc_0", "20", "0\n");
  add_pmu("uncore_imc_2", "22", "0\n");
  add_pmu("uncore_imc_free_running_0", "40", "0\n");
  add_pmu("uncore_imc_", "41", "0\n");
  add_pmu("uncore_hbm_1", "50", "0\n");
  add_pmu("cpu", "4", "0\n");
  u = make_reader(&x86_uncore_profile_imc_spr);
  assert(x86_uncore_perf_attach(&u) == 4);
  assert(strcmp(u.dev[0].name, "mbox0") == 0 && strcmp(u.dev[1].name, "mbox2") == 0);
  assert(strcmp(u.dev[2].name, "mbox10") == 0 && strcmp(u.dev[3].name, "hbm1") == 0);
  /* Every box present, never a fixed count: one fd per CAS event per box. */
  assert(u.n_open == 8 && g_fake_perf_n == 8);
  assert(fake_perf_find(30, 0xcf05, 0) >= 0 && fake_perf_find(50, 0xf005, 0) >= 0);
  assert(fake_perf_find(40, 0xcf05, 0) < 0 && fake_perf_find(41, 0xcf05, 0) < 0);
  tree_done(&u);
  printf("test_discovery_by_prefix passed\n");
}

static void test_never_pinned_scaled_format(void)
{
  struct x86_uncore_perf u;
  int i;

  tree_init();
  add_pmu("uncore_imc_0", "20", "0\n");
  u = make_reader(&x86_uncore_profile_imc_icx);
  assert(x86_uncore_perf_attach(&u) == 1 && strcmp(u.dev[0].name, "mdev0") == 0);
  for (i = 0; i < g_fake_perf_n; i++) {
    assert(!g_fake_perf_calls[i].pinned && !g_fake_perf_calls[i].exclusive);
    assert(g_fake_perf_calls[i].read_format ==
           (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING));
  }
  assert(fake_perf_find(20, 0x0f04, 0) >= 0 && fake_perf_find(20, 0x3004, 0) >= 0);
  tree_done(&u);
  printf("test_never_pinned_scaled_format passed\n");
}

static void test_sysfs_named_before_raw(void)
{
  struct x86_uncore_perf u;

  tree_init();
  add_pmu("uncore_imc_0", "20", "0\n");
  /* A sysfs name wins over the raw table (a distinct umask proves which one opened). */
  add_file("uncore_imc_0", "events/cas_count_read", "event=0x04,umask=0x03\n");
  add_file("uncore_imc_0", "events/cas_count_read.scale", "6.103515625e-5\n");
  /* An unplaceable sysfs term falls back to the raw encoding. */
  add_file("uncore_imc_0", "events/cas_count_write", "event=0x04,bogus=1\n");
  u = make_reader(&x86_uncore_profile_imc_spr);
  assert(x86_uncore_perf_attach(&u) == 1);
  assert(fake_perf_find(20, 0x0304, 0) >= 0);
  assert(fake_perf_find(20, 0xcf05, 0) < 0);
  assert(fake_perf_find(20, 0xf005, 0) >= 0);
  tree_done(&u);
  printf("test_sysfs_named_before_raw passed\n");
}

static void test_cpumask_cpus_summed_and_scaled(void)
{
  struct x86_uncore_perf u;
  int r3, r40, w3, w40;

  tree_init();
  add_pmu("uncore_imc_0", "20", "3,40\n");
  u = make_reader(&x86_uncore_profile_imc_spr);
  assert(x86_uncore_perf_attach(&u) == 1 && u.n_open == 4);
  r3 = fake_perf_find(20, 0xcf05, 3);
  r40 = fake_perf_find(20, 0xcf05, 40);
  w3 = fake_perf_find(20, 0xf005, 3);
  w40 = fake_perf_find(20, 0xf005, 40);
  assert(r3 >= 0 && r40 >= 0 && w3 >= 0 && w40 >= 0 && fake_perf_find(20, 0xcf05, 0) < 0);
  fake_perf_push(r3, 100, 1000, 1000);
  fake_perf_push(r40, 50, 1000, 1000);
  fake_perf_push(w3, 10, 1000, 1000);
  fake_perf_push(w40, 10, 1000, 1000);
  read_and_publish(&u);
  assert(val_of("mbox0", "dram_cas_reads") == 0);
  /* CPU 3 ran half the time: 200 x 1000 / 500 = 400; CPU 40 adds 100 unscaled. */
  fake_perf_push(r3, 300, 2000, 1500);
  fake_perf_push(r40, 150, 2000, 2000);
  fake_perf_push(w3, 20, 2000, 2000);
  fake_perf_push(w40, 20, 2000, 2000);
  read_and_publish(&u);
  assert(val_of("mbox0", "dram_cas_reads") == 500);
  assert(val_of("mbox0", "dram_cas_writes") == 20);
  assert(code_of("mbox0", "hbm_cas_reads") == STATS_SENTINEL_NOT_PRESENT);
  /* Sharing times: enabled and running deltas summed over the four fds. */
  assert(val_of("mbox0", "pmu_enabled_time") == 4000);
  assert(val_of("mbox0", "pmu_running_time") == 3500);
  /* A drop re-seeds: the key reports COUNTER_RESET and its lifetime never decreases. */
  fake_perf_push(r3, 5, 3000, 2500);
  fake_perf_push(r40, 250, 3000, 3000);
  fake_perf_push(w3, 30, 3000, 3000);
  fake_perf_push(w40, 30, 3000, 3000);
  read_and_publish(&u);
  assert(code_of("mbox0", "dram_cas_reads") == STATS_SENTINEL_COUNTER_RESET);
  assert(val_of("mbox0", "dram_cas_writes") == 40);
  assert(u.dev[0].life[0] == 600);
  /* Lifetimes go out modulo 2^48 for the W=48 keys. */
  u.dev[0].life[1] = (1ULL << 48) + 7;
  fake_perf_push(w3, 30, 4000, 4000);
  fake_perf_push(w40, 30, 4000, 4000);
  read_and_publish(&u);
  assert(val_of("mbox0", "dram_cas_writes") == 7);
  assert(code_of("mbox0", "dram_cas_reads") == STATS_SENTINEL_READ_FAILED);
  tree_done(&u);
  printf("test_cpumask_cpus_summed_and_scaled passed\n");
}

static void test_hbm_rows(void)
{
  struct x86_uncore_perf u;
  int r, w;

  tree_init();
  add_pmu("uncore_hbm_0", "50", "0,56\n");
  add_pmu("uncore_hbm_31", "81", "0,56\n");
  u = make_reader(&x86_uncore_profile_imc_spr);
  assert(x86_uncore_perf_attach(&u) == 2 && u.n_open == 8);
  r = fake_perf_find(81, 0xcf05, 56);
  w = fake_perf_find(81, 0xf005, 56);
  assert(r >= 0 && w >= 0);
  fake_perf_push(r, 10, 100, 100);
  fake_perf_push(w, 10, 100, 100);
  read_and_publish(&u);
  fake_perf_push(r, 42, 200, 200);
  fake_perf_push(w, 11, 200, 200);
  read_and_publish(&u);
  assert(strcmp(u.dev[1].name, "hbm31") == 0);
  assert(code_of("hbm31", "dram_cas_reads") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of("hbm31", "dram_cas_writes") == STATS_SENTINEL_NOT_PRESENT);
  /* CPU 0's fds of hbm31 had no sample: READ_FAILED, never a partial sum. */
  assert(code_of("hbm31", "hbm_cas_reads") == STATS_SENTINEL_READ_FAILED);
  assert(code_of("hbm31", "pmu_enabled_time") == STATS_SENTINEL_READ_FAILED);
  fake_perf_push(fake_perf_find(81, 0xcf05, 0), 0, 0, 0);
  fake_perf_push(fake_perf_find(81, 0xf005, 0), 0, 0, 0);
  fake_perf_push(r, 50, 300, 300);
  fake_perf_push(w, 12, 300, 300);
  read_and_publish(&u);
  /* CPU 0 seeded; CPU 56 kept counting across the failed reads: 32 + 8. */
  assert(val_of("hbm31", "hbm_cas_reads") == 40);
  assert(val_of("hbm31", "hbm_cas_writes") == 2);
  tree_done(&u);
  printf("test_hbm_rows passed\n");
}

static void test_fail_soft_sentinels(void)
{
  struct x86_uncore_perf u;
  int r0, r40;

  tree_init();
  add_pmu("uncore_imc_0", "20", "0,40\n");
  add_pmu("uncore_imc_2", "22", "\n");
  add_pmu("uncore_imc_3", "23", "0\n");
  add_file("uncore_imc_3", "format/umask", "\n");
  /* Write CAS denied everywhere. */
  fake_perf_fail_errno(0xf005, -1, EACCES);
  u = make_reader(&x86_uncore_profile_imc_spr);
  assert(x86_uncore_perf_attach(&u) == 3);
  assert(u.dev[1].n_cpus == 0 && u.n_open == 2);
  r0 = fake_perf_find(20, 0xcf05, 0);
  r40 = fake_perf_find(20, 0xcf05, 40);
  fake_perf_push(r0, 1, 10, 10);
  fake_perf_push(r40, 1, 10, 10);
  read_and_publish(&u);
  assert(val_of("mbox0", "dram_cas_reads") == 0);
  assert(code_of("mbox0", "dram_cas_writes") == STATS_SENTINEL_PERMISSION_DENIED);
  assert(val_of("mbox0", "pmu_enabled_time") == 0);
  /* Unreadable cpumask: the row stays and every fed key reports READ_FAILED. */
  assert(code_of("mbox2", "dram_cas_reads") == STATS_SENTINEL_READ_FAILED);
  assert(code_of("mbox2", "pmu_running_time") == STATS_SENTINEL_READ_FAILED);
  assert(code_of("mbox2", "hbm_cas_reads") == STATS_SENTINEL_NOT_PRESENT);
  /* Empty format/umask: the raw encoding cannot be placed. */
  assert(code_of("mbox3", "dram_cas_reads") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of("mbox3", "pmu_enabled_time") == STATS_SENTINEL_NOT_PRESENT);
  /* Enabled but never on a counter: NOT_SCHEDULED; the times still publish. */
  fake_perf_push(r0, 1, 20, 10);
  fake_perf_push(r40, 2, 20, 20);
  read_and_publish(&u);
  assert(code_of("mbox0", "dram_cas_reads") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(val_of("mbox0", "pmu_enabled_time") == 20);
  assert(val_of("mbox0", "pmu_running_time") == 10);
  tree_done(&u);
  printf("test_fail_soft_sentinels passed\n");
}

static void test_one_socket_open_failure_fails_key(void)
{
  struct x86_uncore_perf u;

  tree_init();
  add_pmu("uncore_imc_0", "20", "0,40\n");
  fake_perf_fail_errno(0xcf05, 40, EBUSY);
  u = make_reader(&x86_uncore_profile_imc_gnr);
  assert(x86_uncore_perf_attach(&u) == 1 && u.n_open == 3);
  fake_perf_push(fake_perf_find(20, 0xcf05, 0), 9, 10, 10);
  fake_perf_push(fake_perf_find(20, 0xf005, 0), 9, 10, 10);
  fake_perf_push(fake_perf_find(20, 0xf005, 40), 9, 10, 10);
  read_and_publish(&u);
  assert(code_of("mbox0", "dram_cas_reads") == STATS_SENTINEL_PMU_BUSY);
  assert(val_of("mbox0", "dram_cas_writes") == 0);
  tree_done(&u);
  printf("test_one_socket_open_failure_fails_key passed\n");
}

static void test_not_built_keys(void)
{
  struct x86_uncore_perf u;

  tree_init();
  add_pmu("uncore_imc_0", "20", "0\n");
  add_pmu("amd_df", "11", "0\n");
  u = make_reader(&x86_uncore_profile_imc_skx);
  assert(x86_uncore_perf_attach(&u) == 1);
  assert(fake_perf_find(20, 0x0304, 0) >= 0 && fake_perf_find(20, 0x0c04, 0) >= 0);
  read_and_publish(&u);
  assert(code_of("mbox0", "dram_act_count") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of("mbox0", "dram_pre_count_miss") == STATS_SENTINEL_NOT_BUILT);
  x86_uncore_perf_close(&u);
  u = make_reader(&x86_uncore_profile_df_zen2);
  /* Rome: a row per DRAM channel (8); only channels 0-1 have verified codes. */
  assert(x86_uncore_perf_attach(&u) == 8 && u.n_open == 2);
  assert(fake_perf_find(11, 0x3807, 0) >= 0 && fake_perf_find(11, 0x3847, 0) >= 0);
  read_and_publish(&u);
  assert(code_of("chan2", "dram_data_beats") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of("chan7", "dram_data_beats") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of("chan7", "pmu_enabled_time") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of("chan0", "dram_read_beats") == STATS_SENTINEL_NOT_PRESENT);
  assert(code_of("chan8", "dram_data_beats") == -1); /* no such row */
  tree_done(&u);
  printf("test_not_built_keys passed\n");
}

static void test_df_zen4_read_plus_write_beats(void)
{
  struct x86_uncore_perf u;
  const uint64_t rd0 = 0x1f | (0xfeULL << 8) | (0xfULL << 24);
  const uint64_t wr0 = 0x1f | (0xffULL << 8) | (0xfULL << 24);
  int i;

  tree_init();
  add_pmu("amd_df", "11", "0,64\n");
  add_file("amd_df", "format/event", "config:0-7,32-37\n");
  add_file("amd_df", "format/umask", "config:8-15,24-27\n");
  add_cpu(0, 0);
  add_cpu(64, 1);
  u = make_reader(&x86_uncore_profile_df_zen4);
  /* Genoa / Turin: 12 channel rows; channels 0-3 have verified read and write codes. */
  assert(x86_uncore_perf_attach(&u) == 12 && strcmp(u.dev[0].name, "chan0") == 0);
  assert(u.packages == 2 && u.n_open == 16);
  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 0, 0, 0);
  x86_uncore_perf_read(&u);
  fake_perf_push(fake_perf_find(11, rd0, 0), 1000, 10, 10);
  fake_perf_push(fake_perf_find(11, rd0, 64), 2000, 10, 10);
  fake_perf_push(fake_perf_find(11, wr0, 0), 30, 10, 10);
  fake_perf_push(fake_perf_find(11, wr0, 64), 70, 10, 10);
  for (i = 0; i < g_fake_perf_n; i++)
    if (g_fake_perf_calls[i].config != rd0 && g_fake_perf_calls[i].config != wr0)
      fake_perf_push(i, 5, 10, 10);
  read_and_publish(&u);
  /* Each row splits read and write beats, each summed over both sockets. */
  assert(val_of("chan0", "dram_read_beats") == 3000);
  assert(val_of("chan0", "dram_write_beats") == 100);
  assert(val_of("chan3", "dram_read_beats") == 10);
  assert(code_of("chan0", "dram_data_beats") == STATS_SENTINEL_NOT_PRESENT);
  assert(fake_perf_find(11, 0xdf | (0xffULL << 8) | (0xfULL << 24), 64) >= 0);
  assert(code_of("chan4", "dram_read_beats") == STATS_SENTINEL_NOT_BUILT);
  assert(code_of("chan11", "dram_write_beats") == STATS_SENTINEL_NOT_BUILT);
  tree_done(&u);
  printf("test_df_zen4_read_plus_write_beats passed\n");
}

static void test_df_zen3_single_socket(void)
{
  struct x86_uncore_perf u;

  tree_init();
  add_pmu("amd_df", "11", "0\n");
  add_file("amd_df", "format/event", "config:0-7,32-35,59-60\n");
  add_cpu(0, 0);
  add_cpu(1, 1);
  u = make_reader(&x86_uncore_profile_df_zen3);
  /* An el8 amd_df cpumask names one CPU on a two-socket node: one fd per event and row. */
  assert(x86_uncore_perf_attach(&u) == 8 && u.packages == 2 && u.dev[0].n_cpus == 1);
  assert(u.n_open == 8 && fake_perf_find(11, 0x38c7, 0) >= 0);
  tree_done(&u);
  printf("test_df_zen3_single_socket passed\n");
}

static void test_df_zen3_eight_channels(void)
{
  /* Event bit 8 lands in config bit 32 through the amd_df format (channels 4-7). */
  const uint64_t ch4 = 0x07 | (0x38ULL << 8) | (1ULL << 32);
  const uint64_t ch7 = 0xc7 | (0x38ULL << 8) | (1ULL << 32);
  struct x86_uncore_perf u;
  int i;

  tree_init();
  add_pmu("amd_df", "11", "0\n");
  add_file("amd_df", "format/event", "config:0-7,32-35,59-60\n");
  add_file("amd_df", "format/umask", "config:8-15\n");
  add_cpu(0, 0);
  u = make_reader(&x86_uncore_profile_df_zen3);
  assert(x86_uncore_perf_attach(&u) == 8 && u.n_open == 8);
  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 0, 0, 0);
  x86_uncore_perf_read(&u);
  for (i = 0; i < g_fake_perf_n; i++)
    if (g_fake_perf_calls[i].config != ch4 && g_fake_perf_calls[i].config != ch7)
      fake_perf_push(i, 100, 10, 10);
  fake_perf_push(fake_perf_find(11, ch4, 0), 4000, 10, 10);
  fake_perf_push(fake_perf_find(11, ch7, 0), 7000, 10, 10);
  read_and_publish(&u);
  /* Each Milan channel is its own row with one combined read + write beat count. */
  assert(val_of("chan4", "dram_data_beats") == 4000);
  assert(val_of("chan7", "dram_data_beats") == 7000);
  assert(val_of("chan0", "dram_data_beats") == 100);
  assert(code_of("chan0", "dram_write_beats") == STATS_SENTINEL_NOT_PRESENT);
  assert(val_of("chan7", "pmu_running_time") == 10);
  assert(code_of("chan8", "dram_data_beats") == -1); /* Milan has 8 channels */
  tree_done(&u);
  printf("test_df_zen3_eight_channels passed\n");
}

static void test_reattach_keeps_lifetimes(void)
{
  struct x86_uncore_perf u;
  int r, w, old_r;

  tree_init();
  add_pmu("uncore_imc_0", "20", "0\n");
  u = make_reader(&x86_uncore_profile_imc_icx);
  assert(x86_uncore_perf_attach(&u) == 1);
  r = fake_perf_find(20, 0x0f04, 0);
  w = fake_perf_find(20, 0x3004, 0);
  fake_perf_push(r, 0, 0, 0);
  fake_perf_push(w, 0, 0, 0);
  x86_uncore_perf_read(&u);
  fake_perf_push(r, 77, 5, 5);
  fake_perf_push(w, 0, 5, 5);
  x86_uncore_perf_read(&u);
  old_r = r;
  /* A re-begin (SIGHUP) re-reads type: new fds on the new type, the lifetime stays. */
  add_file("uncore_imc_0", "type", "25\n");
  assert(x86_uncore_perf_attach(&u) == 1);
  assert(fake_perf_closed(old_r));
  r = fake_perf_find(25, 0x0f04, 0);
  w = fake_perf_find(25, 0x3004, 0);
  assert(r >= 0 && w >= 0);
  fake_perf_push(r, 0, 0, 0);
  fake_perf_push(w, 0, 0, 0);
  read_and_publish(&u);
  assert(val_of("mdev0", "dram_cas_reads") == 77);
  assert(val_of("mdev0", "pmu_enabled_time") == 10);
  tree_done(&u);
  printf("test_reattach_keeps_lifetimes passed\n");
}

static void test_count_packages(void)
{
  tree_init();
  assert(x86_uncore_perf_count_packages(g_cpus) == 0);
  add_cpu(0, 0);
  add_cpu(1, 0);
  add_cpu(2, 1);
  add_cpu(3, 1);
  fake_sysfs_mkdir(g_root, "cpu/cpu4");
  fake_sysfs_write(g_root, "cpu/cpufreq/boost", "1\n");
  assert(x86_uncore_perf_count_packages(g_cpus) == 2);
  assert(x86_uncore_perf_count_packages(NULL) == 0);
  fake_sysfs_rm(g_root);
  printf("test_count_packages passed\n");
}

static void test_begin_gating(void)
{
  struct x86_uncore_perf u;
  struct stats_type t;

  tree_init();
  memset(&t, 0, sizeof(t));
  snprintf(t.st_name, sizeof(t.st_name), "intel_x86_uncore_imc_icx");
  u = make_reader(&x86_uncore_profile_imc_icx);
  t.st_enabled = 1;
  assert(x86_uncore_perf_begin(&t, &u, 0) == -1 && !t.st_enabled);
  t.st_enabled = 1;
  assert(x86_uncore_perf_begin(&t, &u, 1) == -1 && !t.st_enabled);
  add_pmu("uncore_imc_0", "20", "0\n");
  t.st_enabled = 1;
  assert(x86_uncore_perf_begin(&t, &u, 1) == 0 && t.st_enabled && u.n == 1);
  assert(x86_uncore_perf_begin(&t, &u, 0) == -1 && u.n == 0 && fake_perf_closed(0));
  tree_done(&u);
  printf("test_begin_gating passed\n");
}

/* Every profile key and both sharing keys are in the collector's KEYS header. */
static void assert_keys_in_schema(const struct x86_uncore_profile *p, const char *schema)
{
  char needle[48];
  int k;

  for (k = 0; k < p->n_keys; k++) {
    snprintf(needle, sizeof(needle), " %s,", p->keys[k]);
    assert(strstr(schema, needle) != NULL);
  }
  assert(strstr(schema, " pmu_enabled_time,E,W=64,U=ns") != NULL);
  assert(strstr(schema, " pmu_running_time,E,W=64,U=ns") != NULL);
}

#define X SCHEMA_DEF
static const char skx_schema[] = JOIN(INTEL_SKX_IMC_KEYS);
static const char icx_schema[] = JOIN(INTEL_ICX_IMC_KEYS);
static const char spr_schema[] = JOIN(INTEL_SPR_IMC_KEYS);
static const char df_schema[] = JOIN(AMD_X86_UNCORE_DF_KEYS);
#undef X

static void test_profiles_match_schema(void)
{
  assert_keys_in_schema(&x86_uncore_profile_imc_skx, skx_schema);
  assert_keys_in_schema(&x86_uncore_profile_imc_icx, icx_schema);
  assert_keys_in_schema(&x86_uncore_profile_imc_spr, spr_schema);
  assert_keys_in_schema(&x86_uncore_profile_imc_gnr, icx_schema);
  assert_keys_in_schema(&x86_uncore_profile_df_zen2, df_schema);
  assert_keys_in_schema(&x86_uncore_profile_df_zen3, df_schema);
  assert_keys_in_schema(&x86_uncore_profile_df_zen4, df_schema);
  printf("test_profiles_match_schema passed\n");
}

int main(void)
{
  test_parse_cpulist();
  test_place_term_multi_range();
  test_discovery_by_prefix();
  test_never_pinned_scaled_format();
  test_sysfs_named_before_raw();
  test_cpumask_cpus_summed_and_scaled();
  test_hbm_rows();
  test_fail_soft_sentinels();
  test_one_socket_open_failure_fails_key();
  test_not_built_keys();
  test_df_zen4_read_plus_write_beats();
  test_df_zen3_single_socket();
  test_df_zen3_eight_channels();
  test_reattach_keeps_lifetimes();
  test_count_packages();
  test_begin_gating();
  test_profiles_match_schema();
  printf("test_x86_uncore_perf passed\n");
  return 0;
}
