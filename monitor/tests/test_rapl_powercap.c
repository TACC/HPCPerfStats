#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "rapl_powercap.h"

static void write_file(const char *path, const char *contents)
{
  FILE *fp = fopen(path, "w");

  assert(fp != NULL);
  fputs(contents, fp);
  fclose(fp);
}

static void test_uj_to_mj(void)
{
  assert(rapl_powercap_uj_to_mj(0) == 0);
  assert(rapl_powercap_uj_to_mj(500) == 1);
  assert(rapl_powercap_uj_to_mj(1000) == 1);
  assert(rapl_powercap_uj_to_mj(1500) == 2);
}

static void test_parse_package_id(void)
{
  unsigned id = 99;

  assert(rapl_powercap_parse_package_id("package-0", &id) == 0);
  assert(id == 0);
  assert(rapl_powercap_parse_package_id("package-1", &id) == 0);
  assert(id == 1);
  assert(rapl_powercap_parse_package_id("core", &id) < 0);
  assert(rapl_powercap_parse_package_id(NULL, &id) < 0);
}

static void test_schema_key_map(void)
{
  assert(strcmp(rapl_powercap_schema_key_from_name("core", 0), "pp0_energy") == 0);
  assert(strcmp(rapl_powercap_schema_key_from_name("core", 1), "core_energy") == 0);
  assert(strcmp(rapl_powercap_schema_key_from_name("dram", 0), "dram_energy") == 0);
  assert(rapl_powercap_schema_key_from_name("uncore", 0) == NULL);
  assert(rapl_powercap_schema_key_from_name(NULL, 0) == NULL);
}

static void test_collect_fixture(void)
{
  char root[] = "/tmp/hpcperfstats_powercap_XXXXXX";
  char pkg0[256], pkg0_core[256], pkg0_dram[256], pkg1[256];
  unsigned long long pkg_mj = 0, core_mj = 0, dram_mj = 0, pp1_mj = 0;
  int has_pkg = 0, has_core = 0, has_dram = 0, has_pp1 = 0;

  assert(mkdtemp(root) != NULL);
  snprintf(pkg0, sizeof(pkg0), "%s/intel-rapl:0", root);
  snprintf(pkg0_core, sizeof(pkg0_core), "%s/intel-rapl:0:0", root);
  snprintf(pkg0_dram, sizeof(pkg0_dram), "%s/intel-rapl:0:1", root);
  snprintf(pkg1, sizeof(pkg1), "%s/intel-rapl:1", root);
  assert(mkdir(pkg0, 0755) == 0);
  assert(mkdir(pkg0_core, 0755) == 0);
  assert(mkdir(pkg0_dram, 0755) == 0);
  assert(mkdir(pkg1, 0755) == 0);

  {
    char p[320];
    snprintf(p, sizeof(p), "%s/name", pkg0);
    write_file(p, "package-0\n");
    snprintf(p, sizeof(p), "%s/energy_uj", pkg0);
    write_file(p, "1250000\n");
    snprintf(p, sizeof(p), "%s/name", pkg0_core);
    write_file(p, "core\n");
    snprintf(p, sizeof(p), "%s/energy_uj", pkg0_core);
    write_file(p, "500000\n");
    snprintf(p, sizeof(p), "%s/name", pkg0_dram);
    write_file(p, "dram\n");
    snprintf(p, sizeof(p), "%s/energy_uj", pkg0_dram);
    write_file(p, "250000\n");
    snprintf(p, sizeof(p), "%s/name", pkg1);
    write_file(p, "package-1\n");
    snprintf(p, sizeof(p), "%s/energy_uj", pkg1);
    write_file(p, "9000000\n");
  }

  assert(rapl_powercap_available_under(root) == 1);
  assert(rapl_powercap_collect_socket_mj_under(root, 0, &pkg_mj, &core_mj, &dram_mj, &has_pkg,
                                               &has_core, &has_dram, &pp1_mj, &has_pp1, 0) == 0);
  assert(has_pkg == 1 && pkg_mj == 1250);
  assert(has_core == 1 && core_mj == 500);
  assert(has_dram == 1 && dram_mj == 250);
  assert(has_pp1 == 0);

  assert(rapl_powercap_collect_socket_mj_under(root, 1, &pkg_mj, &core_mj, &dram_mj, &has_pkg,
                                               &has_core, &has_dram, &pp1_mj, &has_pp1, 0) == 0);
  assert(has_pkg == 1 && pkg_mj == 9000);
  assert(has_core == 0 && has_dram == 0);

  assert(rapl_powercap_collect_socket_mj_under(root, 9, &pkg_mj, &core_mj, &dram_mj, &has_pkg,
                                               &has_core, &has_dram, &pp1_mj, &has_pp1, 0) < 0);

  /* Cleanup best-effort */
  {
    char cmd[384];
    snprintf(cmd, sizeof(cmd), "rm -rf '%s'", root);
    (void)system(cmd);
  }
}

int main(void)
{
  test_uj_to_mj();
  test_parse_package_id();
  test_schema_key_map();
  test_collect_fixture();
  printf("test_rapl_powercap passed\n");
  return 0;
}
