#include <assert.h>
#include <stdio.h>
#include "cpuid.h"
#include "variorum_rapl.h"

processor_t processor = SANDYBRIDGE;

int main(void)
{
  const char *json =
    "{"
    "\"node\":{"
    "\"Socket_0\":{"
    "\"package_joules\":12.5,"
    "\"core_joules\":7.25,"
    "\"dram_joules\":1.5"
    "},"
    "\"Socket_1\":{"
    "\"package_joules\":9.0"
    "}"
    "}"
    "}";
  unsigned long long pkg_mj = 0;
  unsigned long long core_mj = 0;
  unsigned long long dram_mj = 0;
  int has_pkg = 0;
  int has_core = 0;
  int has_dram = 0;
  int rc = variorum_rapl_parse_socket_mj(json, 0, &pkg_mj, &core_mj, &dram_mj,
                                         &has_pkg, &has_core, &has_dram);
  assert(rc == 0);
  assert(has_pkg == 1);
  assert(has_core == 1);
  assert(has_dram == 1);
  assert(pkg_mj == 12500ULL);
  assert(core_mj == 7250ULL);
  assert(dram_mj == 1500ULL);
  rc = variorum_rapl_parse_socket_mj(json, 1, &pkg_mj, &core_mj, &dram_mj,
                                     &has_pkg, &has_core, &has_dram);
  assert(rc == 0);
  assert(has_pkg == 1);
  assert(has_core == 0);
  assert(has_dram == 0);
  assert(pkg_mj == 9000ULL);
  printf("variorum_rapl parser test passed\n");
  return 0;
}
