/* stats_type_get must find every registered st_name whatever the table order.
 *
 * Regression: stats_registry.c lists types by their legacy variable names (llite, mem, opa, ...)
 * while st_name was renamed (lustre_llite, host_mem, host_opa, ...). A binary search over that
 * table missed lustre_*, host_mem, host_mem_job, nvidia_gpu, host_opa and host_proc, so
 * disable_types / HPCPERFSTATS_DISABLE_TYPES / collection_profile minimal silently kept them on
 * (seen on Horizon c122-018, 2026-11-03). The table below mirrors that registry order.
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "stats.h"
#include "stats_registry.h"

static struct stats_type g_types[] = {
    {.st_name = "arm_aarch64_imc"}, {.st_name = "beegfs_client"},      {.st_name = "host_block"},
    {.st_name = "host_cpu"},        {.st_name = "host_cpu_hw"},        {.st_name = "host_ib"},
    {.st_name = "lustre_llite"},    {.st_name = "lustre_mdc"},         {.st_name = "lustre_osc"},
    {.st_name = "host_lnet"},       {.st_name = "host_mem"},           {.st_name = "host_mem_job"},
    {.st_name = "host_net"},        {.st_name = "host_nfs"},           {.st_name = "host_numa"},
    {.st_name = "nvidia_gpu"},      {.st_name = "host_opa"},           {.st_name = "host_proc"},
    {.st_name = "host_ps"},         {.st_name = "host_roofline_peak"}, {.st_name = "host_sysv_shm"},
    {.st_name = "host_tmpfs"},      {.st_name = "host_vfs"},           {.st_name = "host_vm"},
};

#define N_TYPES (sizeof(g_types) / sizeof(g_types[0]))

struct stats_type *const stats_type_table[N_TYPES] = {
    &g_types[0],  &g_types[1],  &g_types[2],  &g_types[3],  &g_types[4],  &g_types[5],
    &g_types[6],  &g_types[7],  &g_types[8],  &g_types[9],  &g_types[10], &g_types[11],
    &g_types[12], &g_types[13], &g_types[14], &g_types[15], &g_types[16], &g_types[17],
    &g_types[18], &g_types[19], &g_types[20], &g_types[21], &g_types[22], &g_types[23],
};
const size_t stats_type_nr = N_TYPES;

static void test_stats_type_get_finds_every_name_in_registry_order(void)
{
  size_t i;

  for (i = 0; i < N_TYPES; i++) {
    struct stats_type *got = stats_type_get(g_types[i].st_name);

    if (got != &g_types[i])
      fprintf(stderr, "stats_type_get(\"%s\") missed\n", g_types[i].st_name);
    assert(got == &g_types[i]);
  }
}

static void test_stats_type_get_rejects_unknown_and_null(void)
{
  assert(stats_type_get(NULL) == NULL);
  assert(stats_type_get("") == NULL);
  assert(stats_type_get("llite") == NULL); /* legacy variable name is not an st_name */
  assert(stats_type_get("host_mem_jo") == NULL);
  assert(stats_type_get("zzz_not_a_type") == NULL);
}

static void test_stats_type_for_each_walks_table_order(void)
{
  size_t i = 0;
  size_t n = 0;
  struct stats_type *type;

  while ((type = stats_type_for_each(&i)) != NULL)
    assert(type == &g_types[n++]);
  assert(n == N_TYPES);
  assert(stats_type_for_each(NULL) == NULL);
}

int main(void)
{
  test_stats_type_get_finds_every_name_in_registry_order();
  test_stats_type_get_rejects_unknown_and_null();
  test_stats_type_for_each_walks_table_order();
  printf("test_stats_type_get: ok\n");
  return 0;
}
