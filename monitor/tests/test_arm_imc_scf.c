/* arm_imc_scf: Grace SCF uncore helpers for arm_aarch64_imc (socket lead CPUs, CAS units).
 *
 * Horizon c122-018 (GB200, 2 Grace sockets) exposes nvidia_scf_pmu_0 (cpumask 0) and
 * nvidia_scf_pmu_1 (cpumask 72) but no dmc/imc PMU, so arm_aarch64_imc disabled itself.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "arm_imc_scf.h"

static char g_root[256];

static void mkpmu(const char *name, const char *cpumask)
{
  char path[512];
  FILE *f;

  snprintf(path, sizeof(path), "%s/%s", g_root, name);
  assert(mkdir(path, 0700) == 0);
  if (cpumask == NULL)
    return;
  snprintf(path, sizeof(path), "%s/%s/cpumask", g_root, name);
  f = fopen(path, "w");
  assert(f != NULL);
  fputs(cpumask, f);
  fclose(f);
}

static void rm_tree(void)
{
  char cmd[600];

  snprintf(cmd, sizeof(cmd), "rm -rf '%s'", g_root);
  assert(system(cmd) == 0);
}

static void test_lead_cpus_ordered_by_socket_index(void)
{
  int cpus[4] = {-1, -1, -1, -1};

  mkpmu("nvidia_scf_pmu_1", "72\n");
  mkpmu("nvidia_scf_pmu_0", "0\n");
  mkpmu("nvidia_pcie_pmu_0", "0\n"); /* other NVIDIA uncore PMUs are ignored */
  mkpmu("armv8_pmuv3_0", NULL);
  assert(arm_imc_scf_lead_cpus(g_root, cpus, 4) == 2);
  assert(cpus[0] == 0 && cpus[1] == 72);
  assert(arm_imc_scf_lead_cpus(g_root, cpus, 1) == 1); /* bounded by cap */
  assert(cpus[0] == 0);
}

static void test_lead_cpus_rejects_bad_inputs(void)
{
  int cpus[2];

  mkpmu("nvidia_scf_pmu_2", NULL);        /* no cpumask: skipped */
  mkpmu("nvidia_scf_pmu_x", "5\n");       /* non-numeric socket: skipped */
  mkpmu("nvidia_scf_pmu_3", "garbage\n"); /* unparsable cpumask: skipped */
  assert(arm_imc_scf_lead_cpus(g_root, cpus, 2) == 2);
  assert(arm_imc_scf_lead_cpus("/nonexistent/hpcperfstats", cpus, 2) == 0);
  assert(arm_imc_scf_lead_cpus(NULL, cpus, 2) == 0);
  assert(arm_imc_scf_lead_cpus(g_root, NULL, 2) == 0);
  assert(arm_imc_scf_lead_cpus(g_root, cpus, 0) == 0);
}

static void test_cas_units(void)
{
  /* CMEM_RD_DATA counts 32-byte beats; CAS keys are 64-byte transfers. */
  assert(arm_imc_scf_rd_beats_to_cas(0) == 0);
  assert(arm_imc_scf_rd_beats_to_cas(2) == 1);
  assert(arm_imc_scf_rd_beats_to_cas(1000001) == 500000);
  /* CMEM_WR_TOTAL_BYTES counts bytes. */
  assert(arm_imc_scf_wr_bytes_to_cas(63) == 0);
  assert(arm_imc_scf_wr_bytes_to_cas(64) == 1);
  assert(arm_imc_scf_wr_bytes_to_cas(6400) == 100);
}

static void test_begin_mode_pending_until_overlay_has_scf(void)
{
  /* c122-018 regression: arm_aarch64_imc began before the DCGM-deferred overlay was up, saw no
   * SCF and no dmc/imc PMU, and disabled itself for the whole run. */
  assert(arm_imc_scf_begin_mode(1, 0, 2, 0) == ARM_IMC_MODE_PENDING);
  assert(arm_imc_scf_begin_mode(1, 1, 2, 0) == ARM_IMC_MODE_SCF);
  assert(arm_imc_scf_begin_mode(1, 1, 2, 3) == ARM_IMC_MODE_SCF); /* SCF wins over perf */
  assert(arm_imc_scf_begin_mode(1, 0, 2, 3) == ARM_IMC_MODE_PERF);
  assert(arm_imc_scf_begin_mode(0, 0, 0, 3) == ARM_IMC_MODE_PERF);
  /* No overlay build or no SCF PMU: nothing can attach later. */
  assert(arm_imc_scf_begin_mode(0, 0, 2, 0) == ARM_IMC_MODE_DISABLED);
  assert(arm_imc_scf_begin_mode(1, 1, 0, 0) == ARM_IMC_MODE_DISABLED);
  assert(arm_imc_scf_begin_mode(1, 0, 0, 0) == ARM_IMC_MODE_DISABLED);
}

static void test_collect_mode_attaches_once_overlay_ready(void)
{
  assert(arm_imc_scf_collect_mode(ARM_IMC_MODE_PENDING, 0, 2) == ARM_IMC_MODE_PENDING);
  assert(arm_imc_scf_collect_mode(ARM_IMC_MODE_PENDING, 1, 2) == ARM_IMC_MODE_SCF);
  assert(arm_imc_scf_collect_mode(ARM_IMC_MODE_PENDING, 1, 0) == ARM_IMC_MODE_PENDING);
  /* Settled modes never change in collect. */
  assert(arm_imc_scf_collect_mode(ARM_IMC_MODE_PERF, 1, 2) == ARM_IMC_MODE_PERF);
  assert(arm_imc_scf_collect_mode(ARM_IMC_MODE_SCF, 0, 2) == ARM_IMC_MODE_SCF);
  assert(arm_imc_scf_collect_mode(ARM_IMC_MODE_DISABLED, 1, 2) == ARM_IMC_MODE_DISABLED);
}

int main(void)
{
  snprintf(g_root, sizeof(g_root), "%s/test_arm_imc_scf.XXXXXX",
           getenv("TMPDIR") != NULL ? getenv("TMPDIR") : "/tmp");
  assert(mkdtemp(g_root) != NULL);
  test_lead_cpus_ordered_by_socket_index();
  test_lead_cpus_rejects_bad_inputs();
  test_cas_units();
  test_begin_mode_pending_until_overlay_has_scf();
  test_collect_mode_attaches_once_overlay_ready();
  rm_tree();
  printf("test_arm_imc_scf: ok\n");
  return 0;
}
