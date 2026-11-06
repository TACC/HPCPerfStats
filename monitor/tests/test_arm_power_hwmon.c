/* arm_power_hwmon: ACPI "<Name> Power Socket <N>" meter index over fixture sysfs trees.
 * Label sets from round 2 findings 16/20/22/28: GB200 (2 sockets, CPU/Grace/SysIO), GH200
 * (1 socket, Module/Grace/CPU/SysIO, EL9 device/ layout), Grace/Grace (2 sockets, no Module). */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "arm_power_hwmon.h"

#define PATH_SZ 512

static void mkdir_p(const char *path)
{
  char tmp[PATH_SZ];
  char *p;

  snprintf(tmp, sizeof(tmp), "%s", path);
  for (p = tmp + 1; *p != '\0'; p++) {
    if (*p == '/') {
      *p = '\0';
      assert(mkdir(tmp, 0700) == 0 || errno == EEXIST);
      *p = '/';
    }
  }
  assert(mkdir(tmp, 0700) == 0 || errno == EEXIST);
}

static void write_text(const char *dir, const char *name, const char *text)
{
  char path[PATH_SZ];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", dir, name);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(text, f);
  fclose(f);
}

/* One meter: hwmon<idx>[/device] with power1_oem_info + power1_average (uW). uw < 0 = no value
 * file. */
static void add_meter(const char *root, int idx, int el9, const char *label, long long uw)
{
  char dir[PATH_SZ];
  char val[32];

  snprintf(dir, sizeof(dir), "%s/class/hwmon/hwmon%d%s", root, idx, el9 ? "/device" : "");
  mkdir_p(dir);
  write_text(dir, "power1_oem_info", label);
  if (uw >= 0) {
    snprintf(val, sizeof(val), "%lld\n", uw);
    write_text(dir, "power1_average", val);
  }
}

static char *make_root(void)
{
  static char root[64];

  snprintf(root, sizeof(root), "/tmp/test_arm_power_hwmon_XXXXXX");
  assert(mkdtemp(root) != NULL);
  return root;
}

static void rm_root(const char *root)
{
  char cmd[PATH_SZ];

  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
  assert(system(cmd) == 0);
}

static double watts(struct arm_power_hwmon *h, int kind, int sock)
{
  return arm_power_hwmon_read_w(h, arm_power_hwmon_find(h, kind, sock));
}

static void test_parse_label(void)
{
  int kind = -1, sock = -1;

  assert(arm_power_hwmon_parse_label("CPU Power Socket 0\n", &kind, &sock) == 0);
  assert(kind == ARM_POWER_CPU && sock == 0);
  assert(arm_power_hwmon_parse_label("Grace Power Socket 1", &kind, &sock) == 0);
  assert(kind == ARM_POWER_SOCKET && sock == 1);
  assert(arm_power_hwmon_parse_label("SysIO Power Socket 1\n", &kind, &sock) == 0);
  assert(kind == ARM_POWER_SYSIO);
  assert(arm_power_hwmon_parse_label("Module Power Socket 0\n", &kind, &sock) == 0);
  assert(kind == ARM_POWER_MODULE);
  /* Well-formed but unknown name (e.g. a future Vera meter): 1, socket still parsed. */
  sock = -1;
  assert(arm_power_hwmon_parse_label("Vera Power Socket 3\n", &kind, &sock) == 1);
  assert(sock == 3);
  /* Not a socket meter / malformed. */
  assert(arm_power_hwmon_parse_label("Power Meter\n", &kind, &sock) == -1);
  assert(arm_power_hwmon_parse_label(" Power Socket 0", &kind, &sock) == -1);
  assert(arm_power_hwmon_parse_label("CPU Power Socket x", &kind, &sock) == -1);
  assert(arm_power_hwmon_parse_label("CPU Power Socket 0 extra", &kind, &sock) == -1);
  assert(arm_power_hwmon_parse_label("CPU Power Socket 99", &kind, &sock) == -1);
  assert(arm_power_hwmon_parse_label("cpu power socket 0", &kind, &sock) == -1);
  assert(arm_power_hwmon_parse_label(NULL, &kind, &sock) == -1);
}

/* GB200 on EL10 (c122-018): attributes directly in hwmonN, hwmon numbers shuffled. */
static void test_gb200_el10_shuffled(void)
{
  char *root = make_root();
  struct arm_power_hwmon h;

  add_meter(root, 12, 0, "SysIO Power Socket 1\n", 6200000);
  add_meter(root, 2, 0, "Grace Power Socket 0\n", 88500000);
  add_meter(root, 9, 0, "CPU Power Socket 0\n", 42000000);
  add_meter(root, 6, 0, "Grace Power Socket 1\n", 91300000);
  add_meter(root, 10, 0, "CPU Power Socket 1\n", 46000000);
  add_meter(root, 11, 0, "SysIO Power Socket 0\n", 6300000);
  {
    char dir[PATH_SZ];

    /* An unrelated hwmon (no label) is skipped. */
    snprintf(dir, sizeof(dir), "%s/class/hwmon/hwmon0", root);
    mkdir_p(dir);
    write_text(dir, "name", "coretemp\n");
  }

  assert(arm_power_hwmon_open(&h, root) == 6);
  assert(h.nsock == 2);
  assert(watts(&h, ARM_POWER_CPU, 0) == 42.0);
  assert(watts(&h, ARM_POWER_CPU, 1) == 46.0);
  assert(watts(&h, ARM_POWER_SOCKET, 0) == 88.5);
  assert(watts(&h, ARM_POWER_SOCKET, 1) == 91.3);
  assert(watts(&h, ARM_POWER_SYSIO, 0) == 6.3);
  assert(watts(&h, ARM_POWER_SYSIO, 1) == 6.2);
  /* No Module meter on GB200: never mapped from Grace (finding 22). */
  assert(arm_power_hwmon_find(&h, ARM_POWER_MODULE, 0) == -1);
  assert(arm_power_hwmon_read_w(&h, -1) == 0.0);
  arm_power_hwmon_close(&h);
  assert(h.n == 0);
  rm_root(root);
}

/* GH200 on EL9 (Vista c642-041): attributes under hwmonN/device, one socket, all four meters. */
static void test_gh200_el9_device_layout(void)
{
  char *root = make_root();
  struct arm_power_hwmon h;

  add_meter(root, 1, 1, "Module Power Socket 0\n", 130300000);
  add_meter(root, 2, 1, "Grace Power Socket 0\n", 46200000);
  add_meter(root, 3, 1, "CPU Power Socket 0\n", 44300000);
  add_meter(root, 4, 1, "SysIO Power Socket 0\n", 600000);

  assert(arm_power_hwmon_open(&h, root) == 4);
  assert(h.nsock == 1);
  assert(watts(&h, ARM_POWER_MODULE, 0) == 130.3);
  assert(watts(&h, ARM_POWER_SOCKET, 0) == 46.2);
  assert(watts(&h, ARM_POWER_CPU, 0) == 44.3);
  assert(watts(&h, ARM_POWER_SYSIO, 0) == 0.6);
  arm_power_hwmon_close(&h);
  rm_root(root);
}

/* Grace/Grace (Vista i618-141): two sockets, CPU/Grace/SysIO, unknown label ignored. */
static void test_grace_grace_and_unknown_label(void)
{
  char *root = make_root();
  struct arm_power_hwmon h;

  add_meter(root, 2, 1, "Grace Power Socket 0\n", 76350000);
  add_meter(root, 3, 1, "CPU Power Socket 0\n", 49740000);
  add_meter(root, 4, 1, "SysIO Power Socket 0\n", 4100000);
  add_meter(root, 5, 1, "Grace Power Socket 1\n", 67640000);
  add_meter(root, 6, 1, "CPU Power Socket 1\n", 40710000);
  add_meter(root, 7, 1, "SysIO Power Socket 1\n", 4750000);
  add_meter(root, 8, 1, "Vera Power Socket 1\n", 1000000);

  assert(arm_power_hwmon_open(&h, root) == 6);
  assert(h.nsock == 2);
  assert(watts(&h, ARM_POWER_SOCKET, 1) == 67.64);
  assert(watts(&h, ARM_POWER_CPU, 0) == 49.74);
  assert(arm_power_hwmon_find(&h, ARM_POWER_MODULE, 1) == -1);
  arm_power_hwmon_close(&h);
  rm_root(root);
}

/* Missing value file: meter not opened. Unreadable/garbage value at read time: 0 W. Duplicate
 * label: first one wins. No hwmon class at all: 0 meters. */
static void test_missing_garbage_duplicate_and_empty(void)
{
  char *root = make_root();
  struct arm_power_hwmon h;
  char dir[PATH_SZ];
  int slot;

  add_meter(root, 1, 0, "CPU Power Socket 0\n", -1);
  add_meter(root, 2, 0, "Grace Power Socket 0\n", 50000000);
  add_meter(root, 3, 0, "Grace Power Socket 0\n", 99000000);
  assert(arm_power_hwmon_open(&h, root) == 1);
  assert(arm_power_hwmon_find(&h, ARM_POWER_CPU, 0) == -1);
  slot = arm_power_hwmon_find(&h, ARM_POWER_SOCKET, 0);
  assert(slot >= 0);
  assert(arm_power_hwmon_read_w(&h, slot) == 50.0 || arm_power_hwmon_read_w(&h, slot) == 99.0);
  /* Value turns into garbage after open: one pread returns 0 W (one-shot warn). */
  snprintf(dir, sizeof(dir), "%s/class/hwmon/hwmon2", root);
  write_text(dir, "power1_average", "n/a\n");
  snprintf(dir, sizeof(dir), "%s/class/hwmon/hwmon3", root);
  write_text(dir, "power1_average", "n/a\n");
  assert(arm_power_hwmon_read_w(&h, slot) == 0.0);
  assert(h.m[slot].read_warned == 1);
  assert(arm_power_hwmon_read_w(&h, slot) == 0.0);
  arm_power_hwmon_close(&h);
  rm_root(root);

  root = make_root();
  assert(arm_power_hwmon_open(&h, root) == 0);
  assert(h.nsock == 0);
  assert(arm_power_hwmon_open(&h, NULL) == 0);
  assert(arm_power_hwmon_open(NULL, root) == 0);
  assert(arm_power_hwmon_find(NULL, 0, 0) == -1);
  assert(arm_power_hwmon_read_w(NULL, 0) == 0.0);
  arm_power_hwmon_close(NULL);
  rm_root(root);
}

int main(void)
{
  test_parse_label();
  test_gb200_el10_shuffled();
  test_gh200_el9_device_layout();
  test_grace_grace_and_unknown_label();
  test_missing_garbage_duplicate_and_empty();
  printf("test_arm_power_hwmon passed\n");
  return 0;
}
