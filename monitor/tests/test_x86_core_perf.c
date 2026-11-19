/* x86_core_perf: per-class event resolution, per-CPU opens, sentinel codes, scaled lifetimes
 * and D6 sharing sums on Intel / AMD, driven through the fake sysfs tree and perf layer. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "monitor_log.h"
#include "test_fake_perf.h"
#include "x86_core_perf.h"

#define CPU_TYPE 4
#define SCALED_FORMAT (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING)
#define INTEL_ROW_EVENTS 15 /* 3 architectural + l1d_replacement + 3 hits + 8 FP_ARITH */

static char g_root[512];
static char g_dev[600];

static struct x86_core_perf_cfg cfg(void)
{
  struct x86_core_perf_cfg c;

  memset(&c, 0, sizeof(c));
  c.devices_dir = g_dev;
  c.open_fn = fake_perf_open;
  return c;
}

static void fresh_root(void)
{
  fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_dev, sizeof(g_dev), "%s/devices", g_root);
}

/* The x86 `cpu` PMU as the kernel exposes it: generic aliases only, no model events. */
static void make_tree(int amd, int with_ref_cycles)
{
  fresh_root();
  fake_sysfs_write(g_root, "devices/cpu/type", "4\n");
  fake_sysfs_write(g_root, "devices/cpu/format/event", "config:0-7,32-35\n");
  fake_sysfs_write(g_root, "devices/cpu/format/umask", "config:8-15\n");
  fake_sysfs_write(g_root, "devices/cpu/events/instructions", "event=0xc0\n");
  fake_sysfs_write(g_root, "devices/cpu/events/cpu-cycles", amd ? "event=0x76\n" : "event=0x3c\n");
  if (with_ref_cycles)
    fake_sysfs_write(g_root, "devices/cpu/events/ref-cycles", "event=0x00,umask=0x03\n");
  fake_sysfs_write(g_root, "devices/msr/type", "9\n");
}

static void assert_counting_attr(const struct perf_event_attr *a, uint32_t type, uint64_t config)
{
  assert(a->type == type);
  assert(a->config == config);
  assert(a->read_format == SCALED_FORMAT);
  assert(a->pinned == 0 && a->exclusive == 0);
}

static void test_resolve_intel_server_rows(void)
{
  static const processor_t classes[] = {SKYLAKE_X,       CASCADE_LAKE,   ICELAKE_SERVER,
                                        SAPPHIRE_RAPIDS, EMERALD_RAPIDS, GRANITE_RAPIDS};
  static const uint64_t fp[8] = {0x01c7, 0x02c7, 0x04c7, 0x08c7, 0x10c7, 0x20c7, 0x40c7, 0x80c7};
  struct x86_core_perf_cfg c;
  struct perf_event_attr a[X86_CORE_NEVENTS];
  int ok[X86_CORE_NEVENTS];
  size_t k;
  int e;

  make_tree(0, 1);
  c = cfg();
  for (k = 0; k < sizeof(classes) / sizeof(classes[0]); k++) {
    assert(x86_core_perf_resolve(classes[k], &c, a, ok) == INTEL_ROW_EVENTS);
    assert_counting_attr(&a[X86_CORE_INSTRUCTIONS], CPU_TYPE, 0xc0);
    assert_counting_attr(&a[X86_CORE_CYCLES], CPU_TYPE, 0x3c);
    assert_counting_attr(&a[X86_CORE_REF_CYCLES], CPU_TYPE, 0x0300);
    assert_counting_attr(&a[X86_CORE_L1D_REPLACEMENT], CPU_TYPE, 0x0151);
    /* Real L1 / L2 / L3 hit events on every Intel server class, never the all-loads alias. */
    assert_counting_attr(&a[X86_CORE_L1_HIT], CPU_TYPE, 0x01d1);
    assert_counting_attr(&a[X86_CORE_L2_HIT], CPU_TYPE, 0x02d1);
    assert_counting_attr(&a[X86_CORE_L3_HIT], CPU_TYPE, 0x04d1);
    for (e = X86_CORE_FP_SCALAR_DOUBLE; e <= X86_CORE_FP_512B_SINGLE; e++)
      assert_counting_attr(&a[e], CPU_TYPE, fp[e - X86_CORE_FP_SCALAR_DOUBLE]);
    for (e = X86_CORE_AMD_RET_INSTR; e < X86_CORE_NEVENTS; e++)
      assert(!ok[e]);
  }
}

static void test_resolve_amd_rows(void)
{
  struct x86_core_perf_cfg c;
  struct perf_event_attr a[X86_CORE_NEVENTS];
  int ok[X86_CORE_NEVENTS];
  int e;

  /* Turin: the kernel names ref-cycles, but no AMD class reads it. */
  make_tree(1, 1);
  c = cfg();
  assert(x86_core_perf_resolve(AMD_TURIN, &c, a, ok) == 9);
  assert(!ok[X86_CORE_REF_CYCLES]);
  assert_counting_attr(&a[X86_CORE_INSTRUCTIONS], CPU_TYPE, 0xc0);
  assert_counting_attr(&a[X86_CORE_CYCLES], CPU_TYPE, 0x76);
  assert_counting_attr(&a[X86_CORE_AMD_RET_INSTR], CPU_TYPE, 0xc0);
  assert_counting_attr(&a[X86_CORE_AMD_RET_BRN], CPU_TYPE, 0xc2);
  assert_counting_attr(&a[X86_CORE_AMD_RET_BRN_MISP], CPU_TYPE, 0xc3);
  assert_counting_attr(&a[X86_CORE_AMD_LS_DISPATCH], CPU_TYPE, 0x0729);
  assert_counting_attr(&a[X86_CORE_AMD_FLOPS], CPU_TYPE, 0x0f03);
  assert_counting_attr(&a[X86_CORE_AMD_FLOPS_DP_PD], CPU_TYPE, 0xaf03);
  assert_counting_attr(&a[X86_CORE_AMD_FLOPS_DP_SD], CPU_TYPE, 0x8f03);
  for (e = X86_CORE_L1D_REPLACEMENT; e <= X86_CORE_FP_512B_SINGLE; e++)
    assert(!ok[e]);

  /* Milan / Genoa: total FLOPs with umask 0xff, no precision split. */
  make_tree(1, 0);
  assert(x86_core_perf_resolve(AMD_MILAN, &c, a, ok) == 7);
  assert_counting_attr(&a[X86_CORE_AMD_FLOPS], CPU_TYPE, 0xff03);
  assert(!ok[X86_CORE_AMD_FLOPS_DP_PD] && !ok[X86_CORE_AMD_FLOPS_DP_SD]);
  assert(!ok[X86_CORE_REF_CYCLES]);
  assert(x86_core_perf_resolve(AMD_GENOA, &c, a, ok) == 7);
  assert_counting_attr(&a[X86_CORE_AMD_FLOPS], CPU_TYPE, 0xff03);
  assert(!ok[X86_CORE_REF_CYCLES]);
}

static void test_resolve_generic_only_classes(void)
{
  static const processor_t intel_like[] = {SKYLAKE, SIERRA_FOREST, BROADWELL, (processor_t)77};
  struct x86_core_perf_cfg c;
  struct perf_event_attr a[X86_CORE_NEVENTS];
  int ok[X86_CORE_NEVENTS];
  size_t k;
  int e;

  make_tree(0, 1);
  c = cfg();
  for (k = 0; k < sizeof(intel_like) / sizeof(intel_like[0]); k++) {
    assert(x86_core_perf_resolve(intel_like[k], &c, a, ok) == 3);
    assert(ok[X86_CORE_INSTRUCTIONS] && ok[X86_CORE_CYCLES] && ok[X86_CORE_REF_CYCLES]);
    for (e = X86_CORE_L1D_REPLACEMENT; e < X86_CORE_NEVENTS; e++)
      assert(!ok[e]);
  }
  make_tree(1, 1);
  assert(x86_core_perf_resolve(AMD_ROME, &c, a, ok) == 2); /* AMD: no ref-cycles */
  assert(ok[X86_CORE_INSTRUCTIONS] && ok[X86_CORE_CYCLES] && !ok[X86_CORE_REF_CYCLES]);
}

/* sysfs names win; an architectural event the kernel does not name (or names unparsably)
 * falls back to PERF_TYPE_HARDWARE with the same scaled, unpinned attr. */
static void test_resolve_generic_fallback_and_missing_pmu(void)
{
  struct x86_core_perf_cfg c;
  struct perf_event_attr a[X86_CORE_NEVENTS];
  int ok[X86_CORE_NEVENTS];

  fresh_root();
  fake_sysfs_write(g_root, "devices/cpu/type", "4\n");
  fake_sysfs_write(g_root, "devices/cpu/format/event", "config:0-7\n");
  fake_sysfs_write(g_root, "devices/cpu/events/instructions", "event=0xc0\n");
  c = cfg();
  assert(x86_core_perf_resolve(SAPPHIRE_RAPIDS, &c, a, ok) == INTEL_ROW_EVENTS);
  assert_counting_attr(&a[X86_CORE_INSTRUCTIONS], CPU_TYPE, 0xc0);
  assert_counting_attr(&a[X86_CORE_CYCLES], PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES);
  assert_counting_attr(&a[X86_CORE_REF_CYCLES], PERF_TYPE_HARDWARE, PERF_COUNT_HW_REF_CPU_CYCLES);
  fake_sysfs_write(g_root, "devices/cpu/events/instructions", "bogus=0x1\n");
  assert(x86_core_perf_resolve(SAPPHIRE_RAPIDS, &c, a, ok) == INTEL_ROW_EVENTS);
  assert_counting_attr(&a[X86_CORE_INSTRUCTIONS], PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS);
  /* No cpu PMU (or no type file): -1. */
  fresh_root();
  fake_sysfs_mkdir(g_root, "devices/cpu");
  assert(x86_core_perf_resolve(SAPPHIRE_RAPIDS, &c, a, ok) == -1);
  fresh_root();
  fake_sysfs_write(g_root, "devices/msr/type", "9\n");
  assert(x86_core_perf_resolve(AMD_TURIN, &c, a, ok) == -1);
  assert(x86_core_perf_resolve(AMD_TURIN, &c, NULL, ok) == -1);
  assert(x86_core_perf_resolve(AMD_TURIN, &c, a, NULL) == -1);
}

static void test_begin_opens_per_cpu_never_pinned(void)
{
  struct x86_core_perf_cfg c;
  struct x86_core_counts m;
  int i, cpu;

  make_tree(0, 1);
  fake_perf_reset();
  c = cfg();
  assert(x86_core_perf_begin(2, SAPPHIRE_RAPIDS, &c) == 0);
  assert(x86_core_perf_ready());
  assert(g_fake_perf_n == 2 * INTEL_ROW_EVENTS);
  for (i = 0; i < g_fake_perf_n; i++) {
    assert(g_fake_perf_calls[i].pinned == 0 && g_fake_perf_calls[i].exclusive == 0);
    assert(g_fake_perf_calls[i].read_format == SCALED_FORMAT);
    assert(g_fake_perf_calls[i].type == CPU_TYPE);
  }
  for (cpu = 0; cpu < 2; cpu++) {
    assert(fake_perf_find(CPU_TYPE, 0x02d1, cpu) >= 0);
    assert(fake_perf_find(CPU_TYPE, 0x80c7, cpu) >= 0);
    assert(fake_perf_find(CPU_TYPE, 0x0300, cpu) >= 0);
  }
  x86_core_perf_collect_cpu(1, &m);
  assert(m.have[X86_CORE_L3_HIT] && !m.have[X86_CORE_AMD_FLOPS]);
  assert(m.code[X86_CORE_AMD_FLOPS] == STATS_SENTINEL_NOT_PRESENT);
  x86_core_perf_cleanup();
  for (i = 0; i < g_fake_perf_n; i++)
    assert(fake_perf_closed(i));
  assert(!x86_core_perf_ready());
  fake_perf_reset();
}

/* AMD sysfs `instructions` is raw 0xc0, the same counter as amd_ret_instr: opened once, both
 * events report its reading and code. */
static void test_amd_shared_counter_opened_once(void)
{
  struct x86_core_perf_cfg c;
  struct x86_core_counts m;
  int ins;

  make_tree(1, 1);
  fake_perf_reset();
  c = cfg();
  assert(x86_core_perf_begin(2, AMD_TURIN, &c) == 0);
  assert(g_fake_perf_n == 2 * 8);
  ins = fake_perf_find(CPU_TYPE, 0xc0, 1);
  assert(ins >= 0);
  fake_perf_push(ins, 0, 0, 0);
  fake_perf_push(ins, 300, 900, 300);
  x86_core_perf_collect_cpu(1, &m);
  x86_core_perf_collect_cpu(1, &m);
  assert(m.have[X86_CORE_INSTRUCTIONS] && m.have[X86_CORE_AMD_RET_INSTR]);
  assert(m.life[X86_CORE_INSTRUCTIONS] == 900ULL && m.life[X86_CORE_AMD_RET_INSTR] == 900ULL);
  assert(m.code[X86_CORE_AMD_RET_INSTR] == STATS_SENTINEL_OK);
  assert(m.code[X86_CORE_AMD_RET_BRN] == STATS_SENTINEL_READ_FAILED); /* nothing pushed */
  assert(!m.have[X86_CORE_REF_CYCLES] && m.code[X86_CORE_REF_CYCLES] == STATS_SENTINEL_NOT_PRESENT);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* Points stdout / stderr (the warn sinks) and the info stream at @p f; saves the old fds. */
static void capture_logs(FILE *f, int saved[2])
{
  fflush(stdout);
  fflush(stderr);
  saved[0] = dup(1);
  saved[1] = dup(2);
  assert(saved[0] >= 0 && saved[1] >= 0);
  assert(dup2(fileno(f), 1) == 1 && dup2(fileno(f), 2) == 2);
  monitor_log_set_stream(f);
}

/* Restores the fds and returns how many captured lines contain @p needle. */
static int release_logs(FILE *f, const int saved[2], const char *needle)
{
  char line[512];
  int n = 0;

  fflush(stdout);
  fflush(stderr);
  fflush(f);
  assert(dup2(saved[0], 1) == 1 && dup2(saved[1], 2) == 2);
  close(saved[0]);
  close(saved[1]);
  monitor_log_set_stream(NULL);
  rewind(f);
  while (fgets(line, sizeof(line), f) != NULL)
    n += strstr(line, needle) != NULL;
  return n;
}

static void begin_with_failed_open(int err, enum stats_sentinel want)
{
  struct x86_core_perf_cfg c = cfg();
  struct x86_core_counts m;
  FILE *f = tmpfile();
  int saved[2], cpu, logged;

  assert(f != NULL);
  fake_perf_reset();
  fake_perf_fail_errno(0x02d1, -1, err); /* l2_hit fails on every CPU */
  capture_logs(f, saved);
  assert(x86_core_perf_begin(4, ICELAKE_SERVER, &c) == 0);
  logged = release_logs(f, saved, "l2_hit open failed on 4 of 4 CPU(s)");
  fclose(f);
#if !defined(RABBITMQ) || defined(DEBUG)
  assert(logged == 1); /* once per event, not per CPU */
#else
  (void)logged; /* release RABBITMQ builds send warnings to syslog */
#endif
  assert(g_fake_perf_n == 4 * (INTEL_ROW_EVENTS - 1));
  for (cpu = 0; cpu < 4; cpu++) {
    x86_core_perf_collect_cpu(cpu, &m);
    assert(!m.have[X86_CORE_L2_HIT] && m.life[X86_CORE_L2_HIT] == 0);
    assert(m.code[X86_CORE_L2_HIT] == (unsigned char)want);
    assert(m.have[X86_CORE_L1_HIT] && m.have[X86_CORE_L3_HIT]);
  }
  x86_core_perf_cleanup();
  fake_perf_reset();
}

static void test_failed_open_codes_logged_once(void)
{
  make_tree(0, 1);
  begin_with_failed_open(EACCES, STATS_SENTINEL_PERMISSION_DENIED);
  begin_with_failed_open(EPERM, STATS_SENTINEL_PERMISSION_DENIED);
  begin_with_failed_open(EBUSY, STATS_SENTINEL_PMU_BUSY);
  begin_with_failed_open(ENOENT, STATS_SENTINEL_NOT_PRESENT);
  begin_with_failed_open(EOPNOTSUPP, STATS_SENTINEL_NOT_PRESENT);
}

static void test_partial_cpu_failure(void)
{
  struct x86_core_perf_cfg c;
  struct x86_core_counts m;

  make_tree(1, 0);
  fake_perf_reset();
  fake_perf_fail_errno(0xff03, 1, EBUSY); /* FLOPs pair taken on CPU 1 only */
  c = cfg();
  assert(x86_core_perf_begin(2, AMD_GENOA, &c) == 0);
  x86_core_perf_collect_cpu(0, &m);
  assert(m.have[X86_CORE_AMD_FLOPS]);
  x86_core_perf_collect_cpu(1, &m);
  assert(!m.have[X86_CORE_AMD_FLOPS] && m.code[X86_CORE_AMD_FLOPS] == STATS_SENTINEL_PMU_BUSY);
  assert(m.have[X86_CORE_AMD_LS_DISPATCH]);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* Seeds every open counter, then one tick: value 100 at 25% running scales to 400. */
static void test_collect_scaled_lifetimes(void)
{
  struct x86_core_perf_cfg c;
  struct x86_core_counts m;
  int i, e;

  make_tree(0, 1);
  fake_perf_reset();
  c = cfg();
  assert(x86_core_perf_begin(2, SAPPHIRE_RAPIDS, &c) == 0);
  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 0, 0, 0);
  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 100, 400, 100);
  x86_core_perf_collect_cpu(0, &m); /* seed */
  for (e = 0; e < INTEL_ROW_EVENTS; e++)
    assert(m.code[e] == STATS_SENTINEL_OK && m.life[e] == 0);
  x86_core_perf_collect_cpu(0, &m);
  for (e = 0; e < INTEL_ROW_EVENTS; e++)
    assert(m.have[e] && m.code[e] == STATS_SENTINEL_OK && m.life[e] == 400ULL);
  x86_core_perf_collect_cpu(1, &m);
  x86_core_perf_collect_cpu(1, &m);
  assert(m.life[X86_CORE_FP_512B_DOUBLE] == 400ULL);
  x86_core_perf_collect_cpu(9, &m); /* out of range */
  assert(!m.have_times && !m.have[X86_CORE_CYCLES]);
  assert(m.code[X86_CORE_CYCLES] == STATS_SENTINEL_NOT_PRESENT);
  x86_core_perf_collect_cpu(0, NULL);
  x86_core_perf_cleanup();
  x86_core_perf_collect_cpu(0, &m); /* not ready */
  assert(!m.have_times && m.code[X86_CORE_CYCLES] == STATS_SENTINEL_NOT_PRESENT);
  fake_perf_reset();
}

/* Seeds every open counter, then one tick of 1000 ns enabled: events whose config is in
 * @p fixed run @p fixed_run ns, the rest @p gen_run ns. */
static void push_sharing_tick(const uint64_t *fixed, int nfixed, uint64_t fixed_run,
                              uint64_t gen_run)
{
  int i, k;

  for (i = 0; i < g_fake_perf_n; i++)
    fake_perf_push(i, 0, 0, 0);
  for (i = 0; i < g_fake_perf_n; i++) {
    int is_fixed = 0;

    for (k = 0; k < nfixed; k++)
      is_fixed |= g_fake_perf_calls[i].config == fixed[k];
    fake_perf_push(i, 10, 1000, is_fixed ? fixed_run : gen_run);
  }
}

/* One CPU of class @p proc: begin, seed (times stay 0), one sharing tick into @p m. */
static void sharing_tick(processor_t proc, int amd, const uint64_t *fixed, int nfixed,
                         uint64_t fixed_run, uint64_t gen_run, struct x86_core_counts *m)
{
  struct x86_core_perf_cfg c = cfg();

  make_tree(amd, !amd);
  fake_perf_reset();
  assert(x86_core_perf_begin(1, proc, &c) == 0);
  push_sharing_tick(fixed, nfixed, fixed_run, gen_run);
  x86_core_perf_collect_cpu(0, m);
  assert(m->have_times && m->pmu_enabled_ns == 0 && m->pmu_solo_ns == 0);
  x86_core_perf_collect_cpu(0, m);
}

/* Intel: instructions / cycles / ref-cycles run on fixed counters (share 1.0); the 12 general
 * events share 8 counters, so 8/12 of their enabled time is the monitor's own oversubscription. */
static void test_sharing_intel_fixed_and_general(void)
{
  static const uint64_t fixed[3] = {0xc0, 0x3c, 0x0300};
  struct x86_core_counts m;

  sharing_tick(SAPPHIRE_RAPIDS, 0, fixed, 3, 1000, 667, &m);
  assert(m.have_times && m.have_solo);
  assert(m.pmu_enabled_ns == 15000ULL);
  assert(m.pmu_running_ns == 3000ULL + 12 * 667);
  assert(m.pmu_solo_ns == 3000ULL + 8000ULL); /* 3 x 1000 + 12 x 1000 x 8/12 */
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* AMD: every event is general, and each event-0x03 FLOP counter takes an adjacent pair.
 * Turin: 5 single + 3 paired = 11 slots on 6 counters; Milan: 5 + 1 paired = 7 slots. */
static void test_sharing_amd_pairs_count_twice(void)
{
  struct x86_core_perf_cfg c;
  struct x86_core_counts m;

  sharing_tick(AMD_TURIN, 1, NULL, 0, 0, 545, &m);
  assert(m.have_solo && m.pmu_enabled_ns == 8000ULL); /* 8 counters (instructions shared) */
  assert(m.pmu_running_ns == 8ULL * 545);
  assert(m.pmu_solo_ns == 4364ULL); /* 8000 x 6/11 = 4363.6 */
  x86_core_perf_cleanup();
  sharing_tick(AMD_MILAN, 1, NULL, 0, 0, 857, &m);
  assert(m.pmu_enabled_ns == 6000ULL);
  assert(m.pmu_solo_ns == 5143ULL); /* 6000 x 6/7 = 5142.9 */
  x86_core_perf_cleanup();
  /* FLOP pair unavailable on every CPU: 5 single slots fit 6 counters, solo share 1.0. */
  make_tree(1, 0);
  fake_perf_reset();
  fake_perf_fail_errno(0xff03, -1, EBUSY);
  c = cfg();
  assert(x86_core_perf_begin(1, AMD_MILAN, &c) == 0);
  push_sharing_tick(NULL, 0, 0, 1000);
  x86_core_perf_collect_cpu(0, &m);
  x86_core_perf_collect_cpu(0, &m);
  assert(m.pmu_enabled_ns == 5000ULL && m.pmu_solo_ns == 5000ULL && m.pmu_running_ns == 5000ULL);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

/* A class without a verified row has no known counter budget: times, but no solo baseline. */
static void test_sharing_unknown_class_no_solo(void)
{
  struct x86_core_counts m;

  sharing_tick(SIERRA_FOREST, 0, NULL, 0, 0, 400, &m);
  assert(m.have_times && !m.have_solo);
  assert(m.pmu_enabled_ns == 3000ULL && m.pmu_running_ns == 1200ULL && m.pmu_solo_ns == 0);
  assert(m.code[X86_CORE_L1_HIT] == STATS_SENTINEL_NOT_PRESENT && !m.have[X86_CORE_L1_HIT]);
  x86_core_perf_cleanup();
  fake_perf_reset();
}

static void test_begin_fails_soft(void)
{
  struct x86_core_perf_cfg c;
  struct x86_core_counts m;

  /* Rome reads instructions + cycles; both name one counter, whose open fails: -1. */
  make_tree(1, 0);
  fake_sysfs_write(g_root, "devices/cpu/events/instructions", "event=0x76\n");
  fake_perf_reset();
  fake_perf_fail(0x76, -1);
  c = cfg();
  assert(x86_core_perf_begin(2, AMD_ROME, &c) == -1);
  assert(!x86_core_perf_ready());
  x86_core_perf_collect_cpu(0, &m);
  assert(!m.have[X86_CORE_CYCLES] && m.code[X86_CORE_CYCLES] == STATS_SENTINEL_NOT_PRESENT);
  fake_perf_reset();
  assert(x86_core_perf_begin(0, SAPPHIRE_RAPIDS, &c) == -1);
  fresh_root();
  assert(x86_core_perf_begin(2, SAPPHIRE_RAPIDS, &c) == -1);
  assert(g_fake_perf_n == 0);
  x86_core_perf_cleanup(); /* idempotent */
  x86_core_perf_cleanup();
}

static void test_event_names(void)
{
  int e;

  for (e = 0; e < X86_CORE_NEVENTS; e++)
    assert(x86_core_perf_event_name(e) != NULL && x86_core_perf_event_name(e)[0] != '\0');
  assert(strcmp(x86_core_perf_event_name(X86_CORE_CYCLES), "cycles") == 0);
  assert(strcmp(x86_core_perf_event_name(X86_CORE_REF_CYCLES), "ref_cycles") == 0);
  assert(strcmp(x86_core_perf_event_name(X86_CORE_L3_HIT), "l3_hit") == 0);
  assert(strcmp(x86_core_perf_event_name(X86_CORE_FP_512B_SINGLE), "fp_512b_single") == 0);
  assert(strcmp(x86_core_perf_event_name(X86_CORE_AMD_FLOPS_DP_SD), "amd_flops_dp_sd") == 0);
  assert(x86_core_perf_event_name(X86_CORE_NEVENTS) == NULL);
  assert(x86_core_perf_event_name(-1) == NULL);
}

int main(void)
{
  fake_sysfs_init(g_root, sizeof(g_root));
  test_resolve_intel_server_rows();
  test_resolve_amd_rows();
  test_resolve_generic_only_classes();
  test_resolve_generic_fallback_and_missing_pmu();
  test_begin_opens_per_cpu_never_pinned();
  test_amd_shared_counter_opened_once();
  test_failed_open_codes_logged_once();
  test_partial_cpu_failure();
  test_collect_scaled_lifetimes();
  test_sharing_intel_fixed_and_general();
  test_sharing_amd_pairs_count_twice();
  test_sharing_unknown_class_no_solo();
  test_begin_fails_soft();
  test_event_names();
  fake_sysfs_rm(g_root);
  printf("test_x86_core_perf passed\n");
  return 0;
}
