/* x86_rapl: per-socket RAPL energy from the perf power PMU with powercap fallback.
 *
 * Readings are running totals in mJ (the old AMD path added a whole cumulative reading per sample
 * with stats_inc), published modulo 2^32 for the W=32 keys. Perf and powercap agree within 0.4% on
 * every measured x86 class; SPR Max (HBM only) has no energy-ram event and no dram zone. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "test_fake_perf.h"
#include "x86_rapl.h"

#define POWER_TYPE 23
#define CFG_PKG 0x02
#define CFG_RAM 0x03
#define COUNTS_PER_J (1ULL << 32) /* energy-pkg scale 2^-32 J */
#define PKG_SCALE "2.3283064365386962890625e-10\n"
#define RAM_SCALE "6.103515625e-05\n" /* 2^-14 J */
#define ALL_DOMAINS                                                                                \
  (X86_RAPL_BIT(X86_RAPL_PKG) | X86_RAPL_BIT(X86_RAPL_CORES) | X86_RAPL_BIT(X86_RAPL_GPU) |        \
   X86_RAPL_BIT(X86_RAPL_RAM))
#define AMD_DOMAINS (X86_RAPL_BIT(X86_RAPL_PKG) | X86_RAPL_BIT(X86_RAPL_CORES))

static char g_root[512];

static void reset_tree(void)
{
  if (g_root[0] != '\0')
    fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  fake_perf_reset();
}

/* Two packages: cpu0-1 on package 0, cpu2-3 on package 1. */
static void add_topology(void)
{
  int cpu;

  for (cpu = 0; cpu < 4; cpu++) {
    char rel[128];

    snprintf(rel, sizeof(rel), "devices/system/cpu/cpu%d/topology/physical_package_id", cpu);
    fake_sysfs_write(g_root, rel, cpu < 2 ? "0\n" : "1\n");
  }
  fake_sysfs_mkdir(g_root, "devices/system/cpu/cpufreq"); /* not a cpuN entry */
}

static void add_power_pmu(const char *cpumask)
{
  fake_sysfs_write(g_root, "bus/event_source/devices/power/type", "23\n");
  fake_sysfs_write(g_root, "bus/event_source/devices/power/format/event", "config:0-7\n");
  fake_sysfs_write(g_root, "bus/event_source/devices/power/cpumask", cpumask);
}

static void add_event(const char *name, const char *code, const char *scale)
{
  char rel[160];

  snprintf(rel, sizeof(rel), "bus/event_source/devices/power/events/%s", name);
  fake_sysfs_write(g_root, rel, code);
  snprintf(rel, sizeof(rel), "bus/event_source/devices/power/events/%s.scale", name);
  fake_sysfs_write(g_root, rel, scale);
  snprintf(rel, sizeof(rel), "bus/event_source/devices/power/events/%s.unit", name);
  fake_sysfs_write(g_root, rel, "Joules\n");
}

static void set_zone_uj(const char *dir, const char *uj)
{
  char rel[160];

  snprintf(rel, sizeof(rel), "class/powercap/%s/energy_uj", dir);
  fake_sysfs_write(g_root, rel, uj);
}

/* Powercap zone <dir> named <name> holding energy_uj; max NULL leaves max_energy_range_uj out. */
static void add_zone(const char *dir, const char *name, const char *uj, const char *max)
{
  char rel[160];

  snprintf(rel, sizeof(rel), "class/powercap/%s/name", dir);
  fake_sysfs_write(g_root, rel, name);
  set_zone_uj(dir, uj);
  if (max != NULL) {
    snprintf(rel, sizeof(rel), "class/powercap/%s/max_energy_range_uj", dir);
    fake_sysfs_write(g_root, rel, max);
  }
}

static void test_perf_scale_per_package_cpu_running_totals(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];
  int pkg0, pkg1, ram0;

  reset_tree();
  add_topology();
  add_power_pmu("1,3\n"); /* the PMU's per-package CPU, not the lowest CPU of the package */
  add_event("energy-pkg", "event=0x02\n", PKG_SCALE);
  add_event("energy-ram", "event=0x03\n", RAM_SCALE);
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 2);
  assert(r.n_sockets == 2 && r.sockets[0].id == 0 && r.sockets[1].id == 1);
  assert(r.sockets[0].cpu == 1 && r.sockets[1].cpu == 3);
  pkg0 = fake_perf_find(POWER_TYPE, CFG_PKG, 1);
  pkg1 = fake_perf_find(POWER_TYPE, CFG_PKG, 3);
  ram0 = fake_perf_find(POWER_TYPE, CFG_RAM, 1);
  assert(pkg0 >= 0 && pkg1 >= 0 && ram0 >= 0);
  assert(g_fake_perf_n == 4); /* no energy-cores / energy-gpu event: nothing else opened */
  assert(g_fake_perf_calls[pkg0].pinned == 0 && g_fake_perf_calls[pkg0].exclusive == 0);
  assert(g_fake_perf_calls[pkg0].read_format ==
         (PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING));

  /* The seed read in begin found no sample, so the first collect seeds: total 0. */
  fake_perf_push(pkg0, 5 * COUNTS_PER_J, 100, 100);
  fake_perf_push(ram0, 16384, 100, 100);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 0);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_OK && s[X86_RAPL_RAM].mj == 0);
  /* No core / gpu event and no powercap zone. */
  assert(s[X86_RAPL_CORES].status == STATS_SENTINEL_NOT_PRESENT);
  assert(s[X86_RAPL_GPU].status == STATS_SENTINEL_NOT_PRESENT);

  /* +2 J then +3 J: the published value is the running total, never the per-read window. */
  fake_perf_push(pkg0, 7 * COUNTS_PER_J, 200, 200);
  fake_perf_push(ram0, 4 * 16384, 200, 200);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].mj == 2000 && s[X86_RAPL_RAM].mj == 3000);
  fake_perf_push(pkg0, 10 * COUNTS_PER_J, 300, 300);
  fake_perf_push(ram0, 4 * 16384, 300, 300);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 5000);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_OK && s[X86_RAPL_RAM].mj == 3000);

  /* No sample (failed read): READ_FAILED; the next good read spans the gap. */
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_READ_FAILED);
  fake_perf_push(pkg0, 12 * COUNTS_PER_J, 500, 500);
  fake_perf_push(ram0, 4 * 16384, 500, 500);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 7000);

  /* Enabled but never on a counter: unmeasured, non-bridgeable. */
  fake_perf_push(pkg0, 12 * COUNTS_PER_J, 600, 500);
  fake_perf_push(ram0, 4 * 16384, 600, 600);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_NOT_SCHEDULED);

  /* Socket 1 is independent of socket 0. */
  fake_perf_push(pkg1, 1, 10, 10);
  x86_rapl_read(&r, 1, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 0);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_READ_FAILED); /* no sample pushed */

  x86_rapl_read(&r, 2, s); /* out of range */
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_READ_FAILED);

  x86_rapl_end(&r);
  assert(fake_perf_closed(pkg0) && fake_perf_closed(pkg1) && fake_perf_closed(ram0));
  assert(r.sockets == NULL && r.n_sockets == 0);
  printf("test_perf_scale_per_package_cpu_running_totals passed\n");
}

static void test_wire_value_wraps_at_32_bits(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];
  int pkg0;

  reset_tree();
  add_topology();
  add_power_pmu("0,2\n");
  add_event("energy-pkg", "event=0x02\n", "0.001\n"); /* 1 count = 1 mJ */
  assert(x86_rapl_begin(&r, g_root, X86_RAPL_BIT(X86_RAPL_PKG), fake_perf_open) == 2);
  pkg0 = fake_perf_find(POWER_TYPE, CFG_PKG, 0);
  assert(pkg0 >= 0 && r.sockets[0].cpu == 0);
  fake_perf_push(pkg0, 0, 1, 1);
  x86_rapl_read(&r, 0, s);
  fake_perf_push(pkg0, (1ULL << 32) - 1, 2, 2);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].mj == 0xffffffffULL);
  fake_perf_push(pkg0, (1ULL << 32) + 5, 3, 3);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 5);
  /* Domains not requested are never opened and stay NOT_PRESENT. */
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_NOT_PRESENT);
  x86_rapl_end(&r);
  printf("test_wire_value_wraps_at_32_bits passed\n");
}

static void test_powercap_fallback_and_wrap(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];

  reset_tree();
  add_topology(); /* no power PMU at all */
  add_zone("intel-rapl:0", "package-0\n", "1000000\n", "2000000\n");
  add_zone("intel-rapl:0:0", "core\n", "50000\n", "2000000\n");
  add_zone("intel-rapl:0:1", "dram\n", "7000\n", NULL); /* no wrap range */
  add_zone("intel-rapl:1", "package-1\n", "9000000\n", "262143328850\n");
  add_zone("intel-rapl-mmio:0", "package-0\n", "1\n", "2\n"); /* not an intel-rapl: zone */
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 2);
  assert(g_fake_perf_n == 0);
  assert(r.sockets[0].cpu == 0 && r.sockets[1].cpu == 2); /* no cpumask: lowest CPU */
  assert(r.sockets[0].dom[X86_RAPL_PKG].source == X86_RAPL_SRC_POWERCAP);

  set_zone_uj("intel-rapl:0", "1500000\n");
  set_zone_uj("intel-rapl:0:0", "150000\n");
  set_zone_uj("intel-rapl:0:1", "9000\n");
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 500);
  assert(s[X86_RAPL_CORES].status == STATS_SENTINEL_OK && s[X86_RAPL_CORES].mj == 100);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_OK && s[X86_RAPL_RAM].mj == 2);
  assert(s[X86_RAPL_GPU].status == STATS_SENTINEL_NOT_PRESENT);

  /* energy_uj wrapped at max_energy_range_uj: 2000000 - 1500000 + 200000 = 700 mJ more. */
  set_zone_uj("intel-rapl:0", "200000\n");
  /* dram dropped with no wrap range: interval lost, re-seeded. */
  set_zone_uj("intel-rapl:0:1", "1000\n");
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 1200);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_COUNTER_RESET);
  set_zone_uj("intel-rapl:0:1", "4000\n");
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_OK && s[X86_RAPL_RAM].mj == 5);
  assert(s[X86_RAPL_PKG].mj == 1200 && s[X86_RAPL_CORES].mj == 100); /* idle: unchanged */

  /* Socket 1: package only. */
  set_zone_uj("intel-rapl:1", "9250000\n");
  x86_rapl_read(&r, 1, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 250);
  assert(s[X86_RAPL_CORES].status == STATS_SENTINEL_NOT_PRESENT);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_NOT_PRESENT);

  /* A garbled read is READ_FAILED and keeps the previous reading. */
  set_zone_uj("intel-rapl:1", "garbage\n");
  x86_rapl_read(&r, 1, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_READ_FAILED);
  set_zone_uj("intel-rapl:1", "9500000\n");
  x86_rapl_read(&r, 1, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 500);
  x86_rapl_end(&r);
  printf("test_powercap_fallback_and_wrap passed\n");
}

/* Mixed sources per domain: perf pkg, powercap dram when energy-ram cannot be opened. */
static void test_per_domain_fallback_and_open_errors(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];
  int old_pkg;

  reset_tree();
  add_topology();
  add_power_pmu("0,2\n");
  add_event("energy-pkg", "event=0x02\n", PKG_SCALE);
  add_event("energy-ram", "event=0x03\n", RAM_SCALE);
  add_zone("intel-rapl:0", "package-0\n", "10\n", "100\n");
  add_zone("intel-rapl:0:0", "dram\n", "1000\n", "1000000\n");
  fake_perf_fail_errno(CFG_RAM, -1, EACCES);
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 2);
  assert(r.sockets[0].dom[X86_RAPL_PKG].source == X86_RAPL_SRC_PERF);
  assert(r.sockets[0].dom[X86_RAPL_RAM].source == X86_RAPL_SRC_POWERCAP);
  /* Socket 1 has no dram zone: the perf open error is what it reports. */
  assert(r.sockets[1].dom[X86_RAPL_RAM].source == X86_RAPL_SRC_NONE);
  set_zone_uj("intel-rapl:0:0", "3000\n");
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_OK && s[X86_RAPL_RAM].mj == 2);
  x86_rapl_read(&r, 1, s);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_PERMISSION_DENIED);

  /* Re-begin (SIGHUP) closes the previous fds before opening again. */
  old_pkg = fake_perf_find(POWER_TYPE, CFG_PKG, 0);
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 2);
  assert(fake_perf_closed(old_pkg));
  x86_rapl_end(&r);
  printf("test_per_domain_fallback_and_open_errors passed\n");
}

/* SPR Max (HBM only): no energy-ram and no dram zone, so dram_energy is NOT_PRESENT. */
static void test_absent_dram_domain(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];
  int pkg0;

  reset_tree();
  add_topology();
  add_power_pmu("0,2\n");
  add_event("energy-pkg", "event=0x02\n", PKG_SCALE);
  add_event("energy-psys", "event=0x05\n", PKG_SCALE);
  add_zone("intel-rapl:0", "package-0\n", "10\n", "100\n");
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 2);
  assert(fake_perf_find(POWER_TYPE, 0x05, 0) < 0); /* psys feeds no key */
  pkg0 = fake_perf_find(POWER_TYPE, CFG_PKG, 0);
  fake_perf_push(pkg0, 1, 1, 1);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK);
  assert(s[X86_RAPL_RAM].status == STATS_SENTINEL_NOT_PRESENT);
  assert(s[X86_RAPL_CORES].status == STATS_SENTINEL_NOT_PRESENT);
  x86_rapl_end(&r);
  printf("test_absent_dram_domain passed\n");
}

/* AMD EPYC: perf energy-pkg; core energy only from the powercap core zone. */
static void test_amd_domains(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];
  int pkg1;

  reset_tree();
  add_topology();
  add_power_pmu("0,2\n");
  add_event("energy-pkg", "event=0x02\n", PKG_SCALE);
  add_event("energy-ram", "event=0x03\n", RAM_SCALE); /* never opened for AMD */
  add_zone("amd-rapl:0", "package-0\n", "10\n", "65532610987\n");
  add_zone("amd-rapl:0:0", "core\n", "100\n", "65532610987\n");
  add_zone("amd-rapl:1", "package-1\n", "10\n", "65532610987\n");
  add_zone("amd-rapl:1:0", "core\n", "200\n", "65532610987\n");
  assert(x86_rapl_begin(&r, g_root, AMD_DOMAINS, fake_perf_open) == 2);
  assert(g_fake_perf_n == 2 && fake_perf_find(POWER_TYPE, CFG_RAM, 0) < 0);
  pkg1 = fake_perf_find(POWER_TYPE, CFG_PKG, 2);
  assert(pkg1 >= 0);
  fake_perf_push(pkg1, 0, 1, 1);
  x86_rapl_read(&r, 1, s);
  set_zone_uj("amd-rapl:1:0", "4200\n");
  fake_perf_push(pkg1, 3 * COUNTS_PER_J, 2, 2);
  x86_rapl_read(&r, 1, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_OK && s[X86_RAPL_PKG].mj == 3000);
  assert(s[X86_RAPL_CORES].status == STATS_SENTINEL_OK && s[X86_RAPL_CORES].mj == 4);
  x86_rapl_end(&r);
  printf("test_amd_domains passed\n");
}

static void test_nothing_readable(void)
{
  struct x86_rapl r = {0};

  reset_tree();
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 0); /* no topology */
  assert(r.n_sockets == 0);
  add_topology();
  add_power_pmu("0,2\n"); /* PMU without events (a VM) and no powercap */
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 0);
  assert(r.n_sockets == 2 && g_fake_perf_n == 0);
  x86_rapl_end(&r);
  /* Root-only energy_uj: the domain reports PERMISSION_DENIED (root bypasses the mode). */
  if (geteuid() != 0) {
    char path[640];
    struct x86_rapl_sample s[X86_RAPL_DOMAINS];

    add_zone("intel-rapl:0", "package-0\n", "10\n", "100\n");
    snprintf(path, sizeof(path), "%s/class/powercap/intel-rapl:0/energy_uj", g_root);
    assert(chmod(path, 0) == 0);
    assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 0);
    x86_rapl_read(&r, 0, s);
    assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_PERMISSION_DENIED);
    x86_rapl_end(&r);
  }
  printf("test_nothing_readable passed\n");
}

/* Pads g_root with directories until it is exactly len characters long. */
static void pad_root(size_t len)
{
  size_t n = strlen(g_root);

  assert(n + 2 <= len && len < sizeof(g_root));
  while (n < len) {
    size_t seg = len - n - 1 > 200 ? 200 : len - n - 1;

    if (len - n - 1 - seg == 1) /* a 1-character tail would be a bare "/" */
      seg--;
    g_root[n++] = '/';
    memset(g_root + n, 'p', seg);
    n += seg;
    g_root[n] = '\0';
  }
}

/* A sysfs path that does not fit its 512-byte buffer is unavailable, never read truncated.
 * Each case plants a file at the exact truncated name, so a truncated read would succeed. */
static void test_overlong_paths_are_unavailable(void)
{
  struct x86_rapl r = {0};
  struct x86_rapl_sample s[X86_RAPL_DOMAINS];
  char base[sizeof(g_root)];

  /* Root of 458: cpuN/topology/physical_package_id is 511 characters (fits), but
   * events/energy-pkg.unit is 512 and .scale 513, truncating to energy-pkg.uni / .sca. */
  reset_tree();
  snprintf(base, sizeof(base), "%s", g_root);
  pad_root(458);
  add_topology();
  add_power_pmu("0,2\n");
  add_event("energy-pkg", "event=0x02\n", PKG_SCALE);
  fake_sysfs_write(g_root, "bus/event_source/devices/power/events/energy-pkg.uni", "Joules\n");
  fake_sysfs_write(g_root, "bus/event_source/devices/power/events/energy-pkg.sca", PKG_SCALE);
  assert(x86_rapl_begin(&r, g_root, X86_RAPL_BIT(X86_RAPL_PKG), fake_perf_open) == 0);
  assert(r.n_sockets == 2 && g_fake_perf_n == 0);
  x86_rapl_read(&r, 0, s);
  assert(s[X86_RAPL_PKG].status == STATS_SENTINEL_NOT_PRESENT);
  x86_rapl_end(&r);

  /* Root of 459: physical_package_id is 512 characters, truncating to physical_package_i. */
  snprintf(g_root, sizeof(g_root), "%s", base);
  pad_root(459);
  add_topology();
  fake_sysfs_write(g_root, "devices/system/cpu/cpu0/topology/physical_package_i", "0\n");
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 0);
  assert(r.n_sockets == 0);

  /* Root of 497: class/powercap is 512 characters, truncating to class/powerca; the power PMU
   * and devices/system/cpu dirs truncate too. Decoys at all three truncated names. */
  snprintf(g_root, sizeof(g_root), "%s", base);
  pad_root(497);
  fake_sysfs_write(g_root, "class/powerca/intel-rapl:0/name", "package-0\n");
  fake_sysfs_write(g_root, "class/powerca/intel-rapl:0/energy_uj", "10\n");
  fake_sysfs_write(g_root, "bus/event_sou/cpumask", "0\n");
  fake_sysfs_write(g_root, "devices/syste/cpu0/topology/physical_package_id", "0\n");
  assert(x86_rapl_begin(&r, g_root, ALL_DOMAINS, fake_perf_open) == 0);
  assert(r.n_sockets == 0 && r.sockets == NULL && g_fake_perf_n == 0);

  snprintf(g_root, sizeof(g_root), "%s", base); /* the next reset_tree removes the padding */
  printf("test_overlong_paths_are_unavailable passed\n");
}

int main(void)
{
  test_perf_scale_per_package_cpu_running_totals();
  test_wire_value_wraps_at_32_bits();
  test_powercap_fallback_and_wrap();
  test_per_domain_fallback_and_open_errors();
  test_absent_dram_domain();
  test_amd_domains();
  test_nothing_readable();
  test_overlong_paths_are_unavailable();
  fake_sysfs_rm(g_root);
  fake_perf_reset();
  printf("test_x86_rapl passed\n");
  return 0;
}
