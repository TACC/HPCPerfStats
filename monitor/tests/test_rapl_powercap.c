#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "rapl_powercap.h"
#include "test_rm_tree.h"

static void write_file(const char *path, const char *contents)
{
  FILE *fp = fopen(path, "w");

  assert(fp != NULL);
  fputs(contents, fp);
  fclose(fp);
}

/* <root>/<zone>/{name,energy_uj[,max_energy_range_uj]}. */
static void make_zone(const char *root, const char *zone, const char *name, const char *uj,
                      const char *max)
{
  char p[320];

  snprintf(p, sizeof(p), "%s/%s", root, zone);
  assert(mkdir(p, 0755) == 0);
  snprintf(p, sizeof(p), "%s/%s/name", root, zone);
  write_file(p, name);
  snprintf(p, sizeof(p), "%s/%s/energy_uj", root, zone);
  write_file(p, uj);
  if (max != NULL) {
    snprintf(p, sizeof(p), "%s/%s/max_energy_range_uj", root, zone);
    write_file(p, max);
  }
}

static void test_parse_package_id(void)
{
  unsigned id = 99;

  assert(rapl_powercap_parse_package_id("package-0", &id) == 0);
  assert(id == 0);
  assert(rapl_powercap_parse_package_id("package-1", &id) == 0);
  assert(id == 1);
  assert(rapl_powercap_parse_package_id("package-0-die-1", &id) < 0);
  assert(rapl_powercap_parse_package_id("core", &id) < 0);
  assert(rapl_powercap_parse_package_id(NULL, &id) < 0);
  printf("test_parse_package_id passed\n");
}

static void test_wrap_delta(void)
{
  uint64_t d = 0;

  assert(rapl_powercap_wrap_delta(100, 250, 1000, &d) == 0 && d == 150);
  assert(rapl_powercap_wrap_delta(100, 100, 0, &d) == 0 && d == 0);
  /* Wrapped once at max_energy_range_uj. */
  assert(rapl_powercap_wrap_delta(900, 50, 1000, &d) == 0 && d == 150);
  /* A drop with no range, or a range below the previous reading, cannot be unwrapped. */
  assert(rapl_powercap_wrap_delta(900, 50, 0, &d) == -ERANGE);
  assert(rapl_powercap_wrap_delta(900, 50, 800, &d) == -ERANGE);
  printf("test_wrap_delta passed\n");
}

static void test_find_zone(const char *root)
{
  static const char *const core[] = {"core", "cores", NULL};
  static const char *const dram[] = {"dram", "ram", NULL};
  char out[512];
  char want[512];

  assert(rapl_powercap_find_zone(root, 0, NULL, out, sizeof(out)) == 0);
  snprintf(want, sizeof(want), "%s/intel-rapl:0", root);
  assert(strcmp(out, want) == 0);
  assert(rapl_powercap_find_zone(root, 0, core, out, sizeof(out)) == 0);
  snprintf(want, sizeof(want), "%s/intel-rapl:0:0", root);
  assert(strcmp(out, want) == 0);
  assert(rapl_powercap_find_zone(root, 0, dram, out, sizeof(out)) == 0);
  snprintf(want, sizeof(want), "%s/intel-rapl:0:1", root);
  assert(strcmp(out, want) == 0);
  /* Package 1 has no children; intel-rapl:10 must not be taken for a child of intel-rapl:1. */
  assert(rapl_powercap_find_zone(root, 1, NULL, out, sizeof(out)) == 0);
  assert(rapl_powercap_find_zone(root, 1, core, out, sizeof(out)) == -ENOENT);
  assert(rapl_powercap_find_zone(root, 9, NULL, out, sizeof(out)) == -ENOENT);
  assert(rapl_powercap_find_zone("/nonexistent/powercap", 0, NULL, out, sizeof(out)) == -ENOENT);
  printf("test_find_zone passed\n");
}

static void test_counter_wrap_and_errors(const char *root)
{
  struct rapl_powercap_counter c;
  char zone[320];
  char p[400];
  uint64_t d = 0;

  snprintf(zone, sizeof(zone), "%s/intel-rapl:0", root);
  snprintf(p, sizeof(p), "%s/energy_uj", zone);
  assert(rapl_powercap_counter_open(&c, zone) == 0);
  assert(c.fd >= 0 && c.max_uj == 2000000 && c.prev_uj == 1250000);
  write_file(p, "1750000\n");
  assert(rapl_powercap_counter_read(&c, &d) == 0 && d == 500000);
  write_file(p, "250000\n"); /* wrapped: 2000000 - 1750000 + 250000 */
  assert(rapl_powercap_counter_read(&c, &d) == 0 && d == 500000);
  write_file(p, "junk\n"); /* failed read keeps the previous reading */
  assert(rapl_powercap_counter_read(&c, &d) == -EIO && d == 0);
  write_file(p, "300000\n");
  assert(rapl_powercap_counter_read(&c, &d) == 0 && d == 50000);
  rapl_powercap_counter_close(&c);
  assert(c.fd == -1);
  rapl_powercap_counter_close(&c);

  /* No max_energy_range_uj: a drop re-seeds with -ERANGE. */
  snprintf(zone, sizeof(zone), "%s/intel-rapl:0:1", root);
  snprintf(p, sizeof(p), "%s/energy_uj", zone);
  assert(rapl_powercap_counter_open(&c, zone) == 0 && c.max_uj == 0);
  write_file(p, "100\n");
  assert(rapl_powercap_counter_read(&c, &d) == -ERANGE && d == 0);
  write_file(p, "400\n");
  assert(rapl_powercap_counter_read(&c, &d) == 0 && d == 300);
  rapl_powercap_counter_close(&c);

  /* Missing energy_uj -> -ENOENT, never a usable counter. */
  snprintf(zone, sizeof(zone), "%s/intel-rapl:1", root);
  snprintf(p, sizeof(p), "%s/energy_uj", zone);
  assert(unlink(p) == 0);
  assert(rapl_powercap_counter_open(&c, zone) == -ENOENT && c.fd == -1);

  /* Root-only energy_uj (mode 0400 root since the PLATYPUS mitigation) -> -EACCES. root bypasses
   * the mode, so only a non-root run can see the denial. */
  if (geteuid() != 0) {
    snprintf(zone, sizeof(zone), "%s/intel-rapl:0:0", root);
    snprintf(p, sizeof(p), "%s/energy_uj", zone);
    assert(chmod(p, 0) == 0);
    assert(rapl_powercap_counter_open(&c, zone) == -EACCES && c.fd == -1);
  }
  printf("test_counter_wrap_and_errors passed\n");
}

int main(void)
{
  char root[] = "/tmp/hpcperfstats_powercap_XXXXXX";

  test_parse_package_id();
  test_wrap_delta();
  assert(mkdtemp(root) != NULL);
  make_zone(root, "intel-rapl:0", "package-0\n", "1250000\n", "2000000\n");
  make_zone(root, "intel-rapl:0:0", "core\n", "500000\n", "2000000\n");
  make_zone(root, "intel-rapl:0:1", "dram\n", "250000\n", NULL);
  make_zone(root, "intel-rapl:1", "package-1\n", "9000000\n", "2000000\n");
  make_zone(root, "intel-rapl:10", "package-10\n", "1\n", NULL);
  make_zone(root, "intel-rapl:10:0", "core\n", "1\n", NULL);
  test_find_zone(root);
  test_counter_wrap_and_errors(root);
  (void)test_rm_tree(root);
  printf("test_rapl_powercap passed\n");
  return 0;
}
