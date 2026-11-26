/* intel_gpu backend failure: every GPU keeps a row of value sentinels (fake sysfs + fake xpumcli). */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "intel_gpu.h"
#include "intel_gpu_pci.h"
#include "intel_gpu_xpumcli.h"
#include "stats.h"
#include "test_rm_tree.h"
#include "test_stats_missing_stub.h"
#include "test_stats_stub.h"
#include "xpum_gpu_dyn.h"

void intel_gpu_test_reset(void);
void intel_gpu_test_set_sysfs_root(const char *root);
void intel_gpu_test_expire_backoff(void);

/* Linked from intel_gpu.c; NULL selects the default xpumcli backend. */
char *intel_gpu_backend = NULL;

#define MAX_ROWS 16

static struct test_stats_stub g_stub;
static struct stats g_dummy_stats;
static char g_rows[MAX_ROWS][16];
static int g_nrows;
static int g_capture_calls;
static int g_backend_up;
static int g_disc_count = 4;
static char g_root_pvc[256];
static char g_root_none[256];

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  int i;

  (void)type;
  for (i = 0; i < g_nrows; i++)
    if (strcmp(g_rows[i], dev) == 0)
      return &g_dummy_stats;
  assert(g_nrows < MAX_ROWS);
  snprintf(g_rows[g_nrows++], sizeof(g_rows[0]), "%s", dev);
  return &g_dummy_stats;
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

/* Down: what xpumcli prints when xpumd is unreachable (exit 0, no device table). */
static int fake_capture(char *const argv[], char *out, size_t out_cap)
{
  size_t pos = 0;
  int i;

  g_capture_calls++;
  if (!g_backend_up) {
    snprintf(out, out_cap, "XPUM Service Status Error\n");
    return 0;
  }
  if (strcmp(argv[1], "discovery") == 0) {
    for (i = 0; i < g_disc_count && pos < out_cap; i++)
      pos +=
          (size_t)snprintf(out + pos, out_cap - pos,
                           "| %d         | Device Name: Intel(R) Data Center GPU Max 1550 |\n", i);
    return 0;
  }
  pos = (size_t)snprintf(out, out_cap, "Timestamp, DeviceId, GPU Utilization (%%)\n");
  for (i = 0; i < g_disc_count && pos < out_cap; i++)
    pos += (size_t)snprintf(out + pos, out_cap - pos, "16:34:10.012, %d, %d.00\n", i, 10 + i);
  return 0;
}

static void write_attr(const char *dir, const char *attr, const char *val)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", dir, attr);
  f = fopen(path, "w");
  assert(f != NULL);
  fprintf(f, "%s\n", val);
  fclose(f);
}

static void add_pci_fn(const char *root, const char *bdf, const char *vendor, const char *device,
                       const char *pci_class)
{
  char dir[512];

  snprintf(dir, sizeof(dir), "%s/bus/pci/devices/%s", root, bdf);
  assert(mkdir(dir, 0700) == 0);
  write_attr(dir, "vendor", vendor);
  write_attr(dir, "device", device);
  write_attr(dir, "class", pci_class);
}

static void make_root(char *root, size_t cap, const char *tag)
{
  const char *tmp = getenv("TMPDIR");
  char path[512];

  snprintf(root, cap, "%s/test_intel_gpu_fallback_%s_XXXXXX",
           tmp != NULL && tmp[0] != '\0' ? tmp : "/tmp", tag);
  assert(mkdtemp(root) != NULL);
  snprintf(path, sizeof(path), "%s/bus", root);
  assert(mkdir(path, 0700) == 0);
  snprintf(path, sizeof(path), "%s/bus/pci", root);
  assert(mkdir(path, 0700) == 0);
  snprintf(path, sizeof(path), "%s/bus/pci/devices", root);
  assert(mkdir(path, 0700) == 0);
  /* Non-GPU Intel function and a Matrox BMC VGA: never counted. */
  add_pci_fn(root, "0000:00:00.0", "0x8086", "0x09a2", "0x060000");
  add_pci_fn(root, "0000:03:00.0", "0x102b", "0x0536", "0x030000");
}

/* Stampede3 c551-004: four Max 1550 functions (8086:0bd5, class 0x038000). */
static void make_fixtures(void)
{
  make_root(g_root_pvc, sizeof(g_root_pvc), "pvc");
  add_pci_fn(g_root_pvc, "0000:18:00.0", "0x8086", "0x0bd5", "0x038000");
  add_pci_fn(g_root_pvc, "0000:42:00.0", "0x8086", "0x0bd5", "0x038000");
  add_pci_fn(g_root_pvc, "0000:6c:00.0", "0x8086", "0x0bd5", "0x038000");
  add_pci_fn(g_root_pvc, "0000:96:00.0", "0x8086", "0x0bd5", "0x038000");
  make_root(g_root_none, sizeof(g_root_none), "none");
}

static void start(const char *root, int backend_up)
{
  assert(unsetenv("HPCPERFSTATS_INTEL_GPU_BACKEND") == 0);
  intel_gpu_backend = NULL;
  intel_gpu_test_reset();
  intel_gpu_test_set_sysfs_root(root);
  intel_gpu_xpumcli_test_set_capture(fake_capture);
  intel_gpu_stats_type.st_enabled = 1;
  g_backend_up = backend_up;
  g_disc_count = 4;
  g_capture_calls = 0;
}

static void collect(void)
{
  g_nrows = 0;
  test_stats_stub_reset(&g_stub);
  test_stats_missing_reset();
  intel_gpu_stats_type.st_collect(&intel_gpu_stats_type);
}

static void assert_rows(int n)
{
  int i;

  assert(g_nrows == n);
  for (i = 0; i < n; i++) {
    char want[16];

    snprintf(want, sizeof(want), "%d", i);
    assert(strcmp(g_rows[i], want) == 0);
  }
}

/* Every row carries `code`; no key holds a value; xpumcli link keys stay NOT_BUILT. */
static void assert_sentinel_rows(int n, enum stats_sentinel code)
{
  assert_rows(n);
  assert(intel_gpu_stats_type.st_enabled);
  assert(g_stub.n == 0);
  assert(test_stats_missing_code(TEST_STATS_MISSING_ALL_KEY) == (unsigned int)code);
  assert(test_stats_missing_code("gpu_pcie_rx_bytes") == STATS_SENTINEL_NOT_BUILT);
  assert(test_stats_missing_code("gpu_xe_link_tx_bytes") == STATS_SENTINEL_NOT_BUILT);
}

static void test_pci_match(void)
{
  assert(intel_gpu_pci_is_max_series(0x8086, 0x0bd5, 0x038000));
  assert(intel_gpu_pci_is_max_series(0x8086, 0x0b69, 0x120000));
  assert(!intel_gpu_pci_is_max_series(0x8086, 0x0bd5, 0x060000));
  assert(!intel_gpu_pci_is_max_series(0x8086, 0x0be0, 0x030000));
  assert(!intel_gpu_pci_is_max_series(0x102b, 0x0bd5, 0x038000));
  assert(intel_gpu_pci_count(g_root_pvc, 32) == 4);
  assert(intel_gpu_pci_count(g_root_pvc, 2) == 2);
  assert(intel_gpu_pci_count(g_root_none, 32) == 0);
  assert(intel_gpu_pci_count("/nonexistent-intel-gpu-sysfs", 32) == 0);
  assert(intel_gpu_pci_count(NULL, 32) == 0);
}

/* xpumd unreachable from the first cycle: 4 sysfs PVC functions -> rows "0".."3". */
static void test_discovery_failure_sysfs_rows(void)
{
  start(g_root_pvc, 0);
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKEND_ERROR);
}

/* After the failure streak, BACKOFF rows and no xpumcli child until the cooldown ends. */
static void test_backoff_after_streak(void)
{
  int calls;

  start(g_root_pvc, 0);
  collect();
  collect();
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKEND_ERROR);
  calls = g_capture_calls;
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKOFF);
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKOFF);
  assert(g_capture_calls == calls);

  intel_gpu_test_expire_backoff();
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKEND_ERROR);
  assert(g_capture_calls == calls + 1);
  /* One failed probe after the cooldown re-arms it. */
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKOFF);
  assert(g_capture_calls == calls + 1);
}

/* xpumd comes back (restart): real values without a daemon restart. */
static void test_recovery_real_values(void)
{
  unsigned long long val;

  start(g_root_pvc, 0);
  collect();
  collect();
  collect();
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKOFF);
  g_backend_up = 1;
  intel_gpu_test_expire_backoff();
  collect();
  assert_rows(4);
  assert(test_stats_stub_find(&g_stub, "gpu_util", &val) && val == 13ULL);
  assert(test_stats_stub_find(&g_stub, "gpu_count", &val) && val == 4ULL);
  assert(test_stats_missing_code(TEST_STATS_MISSING_ALL_KEY) == STATS_SENTINEL_NOT_PRESENT);
  /* The streak reset: a single later failure is BACKEND_ERROR, not BACKOFF. */
  g_backend_up = 0;
  collect();
  assert_sentinel_rows(4, STATS_SENTINEL_BACKEND_ERROR);
}

/* No PVC function and no discovery: no rows, the type stays enabled. */
static void test_no_gpu_no_rows(void)
{
  int cycle;

  start(g_root_none, 0);
  for (cycle = 0; cycle < 5; cycle++) {
    collect();
    assert(g_nrows == 0);
    assert(intel_gpu_stats_type.st_enabled);
  }
}

/* Ids discovered before the failure win over the sysfs count. */
static void test_discovered_ids_reused(void)
{
  start(g_root_pvc, 1);
  g_disc_count = 2;
  collect();
  assert_rows(2);
  g_backend_up = 0;
  collect();
  assert_sentinel_rows(2, STATS_SENTINEL_BACKEND_ERROR);
}

static xpum_result_t fake_xpumInit_fail(void)
{
  return XPUM_GENERIC_ERROR;
}

/* libxpum prepare failure: same sysfs rows, every key BACKEND_ERROR (no NOT_BUILT links). */
static void test_libxpum_prepare_failure(void)
{
  struct xpum_gpu_dyn_test_hooks hooks;

  start(g_root_pvc, 0);
  assert(setenv("HPCPERFSTATS_INTEL_GPU_BACKEND", "libxpum", 1) == 0);
  xpum_gpu_dyn_unload();
  memset(&hooks, 0, sizeof(hooks));
  hooks.xpumInit = fake_xpumInit_fail;
  xpum_gpu_dyn_test_set_hooks(&hooks);
  collect();
  assert_rows(4);
  assert(g_stub.n == 0);
  assert(test_stats_missing_code(TEST_STATS_MISSING_ALL_KEY) == STATS_SENTINEL_BACKEND_ERROR);
  assert(test_stats_missing_code("gpu_pcie_rx_bytes") == 0);
  assert(unsetenv("HPCPERFSTATS_INTEL_GPU_BACKEND") == 0);
  xpum_gpu_dyn_unload();
}

/* A capture buffer too small for the fake output truncates without writing past out_cap. */
static void test_fake_capture_truncates_in_bounds(void)
{
  char *const disc_argv[] = {"xpumcli", "discovery", NULL};
  char *const dump_argv[] = {"xpumcli", "dump", NULL};
  char *const *argvs[] = {disc_argv, dump_argv};
  char buf[512];
  size_t a, k;

  start(g_root_pvc, 1);
  for (a = 0; a < 2; a++) {
    memset(buf, 'Z', sizeof(buf));
    assert(fake_capture(argvs[a], buf, 50) == 0);
    assert(buf[49] == '\0');
    for (k = 50; k < sizeof(buf); k++)
      assert(buf[k] == 'Z');
  }
}

static const struct {
  const char *name;
  void (*fn)(void);
} g_tests[] = {
    {"fake_capture_truncates_in_bounds", test_fake_capture_truncates_in_bounds},
    {"pci_match", test_pci_match},
    {"discovery_failure_sysfs_rows", test_discovery_failure_sysfs_rows},
    {"backoff_after_streak", test_backoff_after_streak},
    {"recovery_real_values", test_recovery_real_values},
    {"no_gpu_no_rows", test_no_gpu_no_rows},
    {"discovered_ids_reused", test_discovered_ids_reused},
    {"libxpum_prepare_failure", test_libxpum_prepare_failure},
};

/* No argument runs every test; one argument runs only the named test. */
int main(int argc, char **argv)
{
  size_t i;
  int ran = 0;

  make_fixtures();
  test_stats_stub_bind(&g_stub);
  for (i = 0; i < sizeof(g_tests) / sizeof(g_tests[0]); i++) {
    if (argc > 1 && strcmp(argv[1], g_tests[i].name) != 0)
      continue;
    g_tests[i].fn();
    ran++;
  }
  test_stats_stub_unbind();
  intel_gpu_test_reset();
  assert(test_rm_tree(g_root_pvc) == 0);
  assert(test_rm_tree(g_root_none) == 0);
  assert(ran > 0);
  printf("test_intel_gpu_fallback passed (%d)\n", ran);
  return 0;
}
