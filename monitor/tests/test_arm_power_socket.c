/* host_power_socket publish: one row per socket, every key present, absent meters publish 0,
 * microwatts -> rounded W. Grace is never mapped to module power (finding 22). */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "arm_power_socket.h"
#include "stats.h"
#include "test_stats_stub.h"

#define X SCHEMA_DEF
static const char g_schema_def[] = JOIN(ARM_POWER_SOCKET_KEYS);
#undef X

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void)type;
  (void)dev;
  return NULL;
}

static struct stats g_dummy_stats;

static void add_meter(const char *root, int idx, const char *label, long long uw)
{
  char dir[512], path[600];
  FILE *f;

  snprintf(dir, sizeof(dir), "%s/class", root);
  assert(mkdir(dir, 0700) == 0 || errno == EEXIST);
  snprintf(dir, sizeof(dir), "%s/class/hwmon", root);
  assert(mkdir(dir, 0700) == 0 || errno == EEXIST);
  snprintf(dir, sizeof(dir), "%s/class/hwmon/hwmon%d", root, idx);
  assert(mkdir(dir, 0700) == 0);
  snprintf(path, sizeof(path), "%s/power1_oem_info", dir);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(label, f);
  fclose(f);
  snprintf(path, sizeof(path), "%s/power1_average", dir);
  f = fopen(path, "w");
  assert(f != NULL);
  fprintf(f, "%lld\n", uw);
  fclose(f);
}

static void slots_for(struct arm_power_hwmon *h, int sock, int slots[ARM_POWER_KIND_COUNT])
{
  int k;

  for (k = 0; k < ARM_POWER_KIND_COUNT; k++)
    slots[k] = arm_power_hwmon_find(h, k, sock);
}

static unsigned long long key_val(const struct test_stats_stub *stub, const char *key)
{
  unsigned long long v = 12345;

  assert(test_stats_stub_find(stub, key, &v));
  return v;
}

static void test_schema_keys_and_order(void)
{
  assert(strstr(g_schema_def, "cpu_power_w,U=W") != NULL);
  assert(strstr(g_schema_def, "socket_power_w,U=W") != NULL);
  assert(strstr(g_schema_def, "sysio_power_w,U=W") != NULL);
  assert(strstr(g_schema_def, "module_power_w,U=W") != NULL);
  /* No cumulative flag: these are gauges. */
  assert(strstr(g_schema_def, ",E") == NULL);
  assert(strcmp(arm_power_socket_keys[ARM_POWER_CPU], "cpu_power_w") == 0);
  assert(strcmp(arm_power_socket_keys[ARM_POWER_SOCKET], "socket_power_w") == 0);
  assert(strcmp(arm_power_socket_keys[ARM_POWER_SYSIO], "sysio_power_w") == 0);
  assert(strcmp(arm_power_socket_keys[ARM_POWER_MODULE], "module_power_w") == 0);
}

/* GB200, two sockets with near-equal watts: both rows publish their own values (D34: the old
 * per-CPU path merged values within 1 W into one socket). */
static void test_gb200_two_sockets(void)
{
  char root[] = "/tmp/test_arm_power_socket_XXXXXX";
  struct arm_power_hwmon h;
  struct test_stats_stub stub;
  int slots[ARM_POWER_KIND_COUNT];
  char cmd[600];

  assert(mkdtemp(root) != NULL);
  add_meter(root, 3, "CPU Power Socket 0\n", 45000000);
  add_meter(root, 4, "CPU Power Socket 1\n", 45400000);
  add_meter(root, 5, "Grace Power Socket 0\n", 88500000);
  add_meter(root, 6, "Grace Power Socket 1\n", 91300000);
  add_meter(root, 7, "SysIO Power Socket 0\n", 6300000);
  add_meter(root, 8, "SysIO Power Socket 1\n", 6200000);
  assert(arm_power_hwmon_open(&h, root) == 6);

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  slots_for(&h, 0, slots);
  arm_power_socket_publish(&g_dummy_stats, &h, slots);
  assert(stub.n == ARM_POWER_KIND_COUNT);
  assert(key_val(&stub, "cpu_power_w") == 45ULL);
  assert(key_val(&stub, "socket_power_w") == 89ULL); /* 88.5 rounds half up */
  assert(key_val(&stub, "sysio_power_w") == 6ULL);
  assert(key_val(&stub, "module_power_w") == 0ULL);

  test_stats_stub_reset(&stub);
  slots_for(&h, 1, slots);
  arm_power_socket_publish(&g_dummy_stats, &h, slots);
  assert(key_val(&stub, "cpu_power_w") == 45ULL);
  assert(key_val(&stub, "socket_power_w") == 91ULL);
  assert(key_val(&stub, "module_power_w") == 0ULL);
  test_stats_stub_unbind();

  arm_power_hwmon_close(&h);
  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
  assert(system(cmd) == 0);
}

/* GH200: module published from its own Module meter. */
static void test_gh200_module(void)
{
  char root[] = "/tmp/test_arm_power_socket_gh_XXXXXX";
  struct arm_power_hwmon h;
  struct test_stats_stub stub;
  int slots[ARM_POWER_KIND_COUNT];
  char cmd[600];

  assert(mkdtemp(root) != NULL);
  add_meter(root, 1, "Module Power Socket 0\n", 130300000);
  add_meter(root, 2, "Grace Power Socket 0\n", 46200000);
  add_meter(root, 3, "CPU Power Socket 0\n", 44300000);
  add_meter(root, 4, "SysIO Power Socket 0\n", 600000);
  assert(arm_power_hwmon_open(&h, root) == 4);

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  slots_for(&h, 0, slots);
  arm_power_socket_publish(&g_dummy_stats, &h, slots);
  assert(key_val(&stub, "module_power_w") == 130ULL);
  assert(key_val(&stub, "socket_power_w") == 46ULL);
  assert(key_val(&stub, "cpu_power_w") == 44ULL);
  assert(key_val(&stub, "sysio_power_w") == 1ULL);
  test_stats_stub_unbind();

  arm_power_hwmon_close(&h);
  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
  assert(system(cmd) == 0);
}

static void test_null_guards(void)
{
  struct test_stats_stub stub;
  int slots[ARM_POWER_KIND_COUNT] = {-1, -1, -1, -1};

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  arm_power_socket_publish(NULL, NULL, slots);
  arm_power_socket_publish(&g_dummy_stats, NULL, NULL);
  assert(stub.n == 0);
  /* No meters at all: every key still present, all 0. */
  arm_power_socket_publish(&g_dummy_stats, NULL, slots);
  assert(stub.n == ARM_POWER_KIND_COUNT);
  assert(key_val(&stub, "cpu_power_w") == 0ULL);
  test_stats_stub_unbind();
}

int main(void)
{
  test_schema_keys_and_order();
  test_gb200_two_sockets();
  test_gh200_module();
  test_null_guards();
  printf("test_arm_power_socket passed\n");
  return 0;
}
