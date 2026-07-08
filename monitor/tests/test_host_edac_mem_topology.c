#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "host_edac_mem_topology.h"

static int write_file(const char *path, const char *content)
{
  FILE *f = fopen(path, "we");

  if (f == NULL)
    return -1;
  if (fputs(content, f) == EOF) {
    fclose(f);
    return -1;
  }
  fclose(f);
  return 0;
}

static int mkdir_p(const char *path)
{
  char buf[512];
  size_t len;
  size_t i;

  if (path == NULL || path[0] == '\0')
    return -1;
  snprintf(buf, sizeof(buf), "%s", path);
  len = strlen(buf);
  for (i = 1; i < len; i++) {
    if (buf[i] != '/')
      continue;
    buf[i] = '\0';
    if (mkdir(buf, 0755) != 0 && buf[0] != '\0') {
      /* exists is ok */
    }
    buf[i] = '/';
  }
  return mkdir(buf, 0755);
}

static void install_dimm(const char *root, const char *mc, const char *dimm,
                         const char *speed, const char *mem_type)
{
  char path[512];

  snprintf(path, sizeof(path), "%s/%s/%s", root, mc, dimm);
  mkdir_p(path);
  snprintf(path, sizeof(path), "%s/%s/%s/dimm_mem_speed", root, mc, dimm);
  assert(write_file(path, speed) == 0);
  snprintf(path, sizeof(path), "%s/%s/%s/dimm_mem_type", root, mc, dimm);
  assert(write_file(path, mem_type) == 0);
}

static void setup_ddr_only(const char *root);
static void setup_hbm_only(const char *root);
static void setup_mixed(const char *root);

static void with_edac_root(const char *fixture_name,
                           void (*setup)(const char *root),
                           void (*verify)(void))
{
  char root[512];
  const char *tmpdir = getenv("TMPDIR");

  if (tmpdir == NULL || tmpdir[0] == '\0')
    tmpdir = "/tmp";
  snprintf(root, sizeof(root), "%s/hpc_edac_%s_%d", tmpdir, fixture_name,
           (int) getpid());
  mkdir_p(root);
  setenv("HPCPERFSTATS_EDAC_MC_ROOT", root, 1);
  setup(root);
  verify();
  unsetenv("HPCPERFSTATS_EDAC_MC_ROOT");
}

static void verify_scan_ddr_only(void)
{
  int has_ddr = 0;
  int has_hbm = 0;

  assert(host_edac_scan_mem_classes(&has_ddr, &has_hbm) == 0);
  assert(has_ddr == 1);
  assert(has_hbm == 0);
}

static void verify_scan_hbm_only(void)
{
  int has_ddr = 0;
  int has_hbm = 0;

  assert(host_edac_scan_mem_classes(&has_ddr, &has_hbm) == 0);
  assert(has_ddr == 0);
  assert(has_hbm == 1);
}

static void verify_scan_mixed(void)
{
  int has_ddr = 0;
  int has_hbm = 0;

  assert(host_edac_scan_mem_classes(&has_ddr, &has_hbm) == 0);
  assert(has_ddr == 1);
  assert(has_hbm == 1);
}

static void test_scan_ddr_only(void)
{
  with_edac_root("ddr_only", setup_ddr_only, verify_scan_ddr_only);
}

static void test_scan_hbm_only(void)
{
  with_edac_root("hbm_only", setup_hbm_only, verify_scan_hbm_only);
}

static void test_scan_mixed(void)
{
  with_edac_root("mixed", setup_mixed, verify_scan_mixed);
}

static void test_scan_missing_mc(void)
{
  char root[512];
  int has_ddr = 1;
  int has_hbm = 1;
  const char *tmpdir = getenv("TMPDIR");

  if (tmpdir == NULL || tmpdir[0] == '\0')
    tmpdir = "/tmp";
  snprintf(root, sizeof(root), "%s/hpc_edac_missing_%d", tmpdir, (int) getpid());
  setenv("HPCPERFSTATS_EDAC_MC_ROOT", root, 1);
  assert(host_edac_scan_mem_classes(&has_ddr, &has_hbm) == 0);
  assert(has_ddr == 0);
  assert(has_hbm == 0);
  unsetenv("HPCPERFSTATS_EDAC_MC_ROOT");
}

typedef struct {
  int count;
  double ddr_bw;
  double hbm_bw;
} bw_sum_t;

static void sum_bw(long long mtps, int is_hbm, void *ctx)
{
  bw_sum_t *s = (bw_sum_t *) ctx;
  double dimm_bw = (double) mtps * 1000000.0 * 8.0;

  s->count++;
  if (is_hbm)
    s->hbm_bw += dimm_bw;
  else
    s->ddr_bw += dimm_bw;
}

static void setup_ddr_only(const char *root)
{
  mkdir_p(root);
  install_dimm(root, "mc0", "dimm0", "4800\n", "DDR5\n");
}

static void setup_hbm_only(const char *root)
{
  mkdir_p(root);
  install_dimm(root, "mc0", "dimm0", "6400\n", "HBM2e\n");
}

static void setup_mixed(const char *root)
{
  mkdir_p(root);
  install_dimm(root, "mc0", "dimm0", "4800\n", "DDR5\n");
  install_dimm(root, "mc0", "dimm1", "6400\n", "HBM3\n");
}

static void verify_foreach_bandwidth(void)
{
  bw_sum_t sum;

  memset(&sum, 0, sizeof(sum));
  assert(host_edac_foreach_dimm(sum_bw, &sum) == 0);
  assert(sum.count == 2);
  assert(sum.ddr_bw > 0.0);
  assert(sum.hbm_bw > 0.0);
}

static void test_foreach_bandwidth(void)
{
  with_edac_root("mixed_bw", setup_mixed, verify_foreach_bandwidth);
}

int main(void)
{
  test_scan_ddr_only();
  test_scan_hbm_only();
  test_scan_mixed();
  test_scan_missing_mc();
  test_foreach_bandwidth();
  printf("test_host_edac_mem_topology passed\n");
  return 0;
}
