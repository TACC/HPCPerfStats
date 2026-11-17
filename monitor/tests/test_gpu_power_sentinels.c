/* GPU and power value sentinels: DCGM per-field status (NOT_SUPPORTED NOT_PRESENT, other
 * READ_FAILED) recorded on the row, RAPL socket read failure mapped from its errno (EACCES /
 * EPERM PERMISSION_DENIED, other READ_FAILED) and absent domains NOT_PRESENT, hwmon meter read failure READ_FAILED (no published 0). Real stats.c state bytes. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "arm_power_hwmon.h"
#include "collect_tier.h"
#include "nvidia_gpu_dcgm_field.h"
#include "rapl_likwid_stats.h"
#include "schema.h"
#include "stats.h"
#include "stats_registry.h"
#include "stats_sentinel.h"

struct stats_type *const stats_type_table[] = {NULL};
const size_t stats_type_nr = 0;
static struct stats_type g_rapl;

/* Fake LIKWID RAPL socket read: rc and which domains the package exposes. */
static int g_rapl_rc;
static int g_rapl_has_dram;

int likwid_rapl_collect_socket_mj(int cpu_id, unsigned int socket_id, unsigned long long *pkg_mj,
                                  unsigned long long *core_mj, unsigned long long *dram_mj,
                                  int *has_pkg, int *has_core, int *has_dram,
                                  unsigned long long *pp1_mj, int *has_pp1)
{
  (void)cpu_id;
  (void)socket_id;
  *pkg_mj = 1000;
  *core_mj = 400;
  *dram_mj = 50;
  *has_pkg = *has_core = 1;
  *has_dram = g_rapl_has_dram;
  if (pp1_mj != NULL && has_pp1 != NULL) {
    *pp1_mj = 0;
    *has_pp1 = 0;
  }
  return g_rapl_rc;
}

static unsigned int state_of(struct stats *s, const char *key)
{
  int i = schema_ref(&s->s_type->st_schema, key);

  assert(i >= 0);
  return s->s_val_state[i];
}

static void test_dcgm_field_status(void)
{
  dcgm_data_t row;

  assert(nvidia_gpu_dcgm_status_sentinel(DCGM_ST_NOT_SUPPORTED) == STATS_SENTINEL_NOT_PRESENT);
  assert(nvidia_gpu_dcgm_status_sentinel(DCGM_ST_NO_DATA) == STATS_SENTINEL_READ_FAILED);
  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_PROF_PIPE_FP64_ACTIVE), "fp64_active") == 0);
  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_DEV_POWER_USAGE), "power_usage") == 0);
  assert(strcmp(nvidia_gpu_dcgm_field_key(DCGM_FI_PROF_NVLINK_RX_BYTES), "gpu_nvlink_rx_bytes") ==
         0);
  assert(nvidia_gpu_dcgm_field_key(0) == NULL);

  memset(&row, 0, sizeof(row));
  nvidia_gpu_row_mark(&row, "fp64_active", STATS_SENTINEL_NOT_PRESENT);
  nvidia_gpu_row_mark(&row, NULL, STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_row_mark(NULL, "x", STATS_SENTINEL_READ_FAILED);
  assert(row.nmiss == 1 && row.miss[0].code == STATS_SENTINEL_NOT_PRESENT);
  for (int i = 0; i < NVIDIA_GPU_ROW_MISS_MAX + 4; i++)
    nvidia_gpu_row_mark(&row, "gpu_util", STATS_SENTINEL_READ_FAILED);
  assert(row.nmiss == NVIDIA_GPU_ROW_MISS_MAX);
}

static void test_rapl_codes(void)
{
  struct stats *s;

  g_rapl_rc = 0;
  g_rapl_has_dram = 0;
  stats_type_mark_not_updated(&g_rapl);
  rapl_likwid_intel_collect_pkg(&g_rapl, "0", 0, 0);
  s = get_current_stats(&g_rapl, "0");
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_OK);
  assert(state_of(s, "pp0_energy") == STATS_SENTINEL_OK);
  assert(state_of(s, "dram_energy") == STATS_SENTINEL_NOT_PRESENT);
  assert(state_of(s, "pp1_energy") == STATS_SENTINEL_NOT_PRESENT);

  g_rapl_rc = -EIO;
  stats_type_mark_not_updated(&g_rapl);
  rapl_likwid_intel_collect_pkg(&g_rapl, "0", 0, 0);
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_READ_FAILED);
  assert(state_of(s, "dram_energy") == STATS_SENTINEL_READ_FAILED);
  assert(s->s_val[schema_ref(&g_rapl.st_schema, "pkg_energy")] == 1000ULL);
}

/* Root-only powercap energy_uj: every key of the package is PERMISSION_DENIED, not READ_FAILED. */
static void test_rapl_permission_denied(void)
{
  static const char *const keys[] = {"pkg_energy", "pp0_energy", "pp1_energy", "dram_energy"};
  static const int errs[] = {-EACCES, -EPERM};
  struct stats *s;

  for (size_t e = 0; e < sizeof(errs) / sizeof(errs[0]); e++) {
    g_rapl_rc = errs[e];
    stats_type_mark_not_updated(&g_rapl);
    rapl_likwid_intel_collect_pkg(&g_rapl, "0", 0, 0);
    s = get_current_stats(&g_rapl, "0");
    for (size_t k = 0; k < sizeof(keys) / sizeof(keys[0]); k++)
      assert(state_of(s, keys[k]) == STATS_SENTINEL_PERMISSION_DENIED);

    stats_type_mark_not_updated(&g_rapl);
    rapl_likwid_amd_collect_socket_cpu(&g_rapl, "1", 0, 1, 0);
    s = get_current_stats(&g_rapl, "1");
    for (size_t k = 0; k < sizeof(keys) / sizeof(keys[0]); k++)
      assert(state_of(s, keys[k]) == STATS_SENTINEL_PERMISSION_DENIED);
  }
}

/* AMD RAPL: only the core-0 row of a socket publishes; absent domains NOT_PRESENT, a failed
 * read READ_FAILED. */
static void test_rapl_amd_codes(void)
{
  struct stats *s;

  g_rapl_rc = 0;
  stats_type_mark_not_updated(&g_rapl);
  rapl_likwid_amd_collect_socket_cpu(&g_rapl, "1", 0, 1, 0);
  s = get_current_stats(&g_rapl, "1");
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_OK);
  assert(state_of(s, "dram_energy") == STATS_SENTINEL_NOT_PRESENT);

  g_rapl_rc = -EIO;
  stats_type_mark_not_updated(&g_rapl);
  rapl_likwid_amd_collect_socket_cpu(&g_rapl, "1", 0, 1, 0);
  assert(state_of(s, "pkg_energy") == STATS_SENTINEL_READ_FAILED);
}

static void test_hwmon_read_failure(void)
{
  struct arm_power_hwmon h;
  double w = 5.0;
  int fds[2];

  memset(&h, 0, sizeof(h));
  assert(pipe(fds) == 0);
  close(fds[1]);
  h.n = 1;
  h.m[0].fd = fds[0]; /* pread on a pipe: ESPIPE */
  assert(arm_power_hwmon_read(&h, 0, &w) == STATS_SENTINEL_READ_FAILED && w == 0.0);
  assert(arm_power_hwmon_read(&h, 1, &w) == STATS_SENTINEL_NOT_PRESENT);
  close(fds[0]);
}

int main(void)
{
  memset(&g_rapl, 0, sizeof(g_rapl));
  snprintf(g_rapl.st_name, sizeof(g_rapl.st_name), "%s", "intel_x86_rapl");
  g_rapl.st_schema_def = "pkg_energy,E pp0_energy,E pp1_energy,E dram_energy,E";
  assert(stats_type_init(&g_rapl) == 0);
  collect_tier_set_enabled(0);
  collect_tier_set_phase(COLLECT_FULL);

  test_dcgm_field_status();
  test_rapl_codes();
  test_rapl_amd_codes();
  test_rapl_permission_denied();
  test_hwmon_read_failure();

  stats_type_destroy(&g_rapl);
  printf("test_gpu_power_sentinels passed\n");
  return 0;
}
