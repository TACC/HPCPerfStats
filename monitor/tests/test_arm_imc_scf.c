/* arm_imc_scf: Grace SCF DRAM traffic for arm_aarch64_imc via direct perf.
 *
 * Findings #6 / #9 / #11: Horizon GB200 has nvidia_scf_pmu_0 (type 25, cpumask 0) and _1
 * (type 15, cpumask 72); Vista Grace-Grace the same with types 24 / 16; Vista GH200 (BIOS F33) one
 * PMU, type 38 cpumask 0, and before F33 none at all. Type numbers are per boot and not ordered by
 * socket, so sockets come from the PMU name index. O17: the PMUs can bind after the daemon
 * started, so a type with no PMU re-scans and attaches later (c642-041 kept the type disabled). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "arm_imc_scf.h"
#include "test_fake_perf.h"

static char g_root[512];
static char g_dev[600];

static void reset_tree(void)
{
  fake_sysfs_rm(g_root);
  fake_sysfs_init(g_root, sizeof(g_root));
  snprintf(g_dev, sizeof(g_dev), "%s/devices", g_root);
  fake_sysfs_mkdir(g_root, "devices/armv8_pmuv3_0");
  fake_sysfs_mkdir(g_root, "devices/nvidia_pcie_pmu_0"); /* other NVIDIA uncore PMUs ignored */
  fake_perf_reset();
}

static void add_scf_pmu(int sock, int type, const char *cpumask)
{
  char rel[128];
  char val[32];

  snprintf(rel, sizeof(rel), "devices/nvidia_scf_pmu_%d/type", sock);
  snprintf(val, sizeof(val), "%d\n", type);
  fake_sysfs_write(g_root, rel, val);
  snprintf(rel, sizeof(rel), "devices/nvidia_scf_pmu_%d/format/event", sock);
  fake_sysfs_write(g_root, rel, "config:0-31\n");
  snprintf(rel, sizeof(rel), "devices/nvidia_scf_pmu_%d/events/cmem_rd_data", sock);
  fake_sysfs_write(g_root, rel, "event=0x1a5\n");
  snprintf(rel, sizeof(rel), "devices/nvidia_scf_pmu_%d/events/cmem_wr_total_bytes", sock);
  fake_sysfs_write(g_root, rel, "event=0x1db\n");
  if (cpumask != NULL) {
    snprintf(rel, sizeof(rel), "devices/nvidia_scf_pmu_%d/cpumask", sock);
    fake_sysfs_write(g_root, rel, cpumask);
  }
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

static void test_begin_mode_has_no_pending_state(void)
{
  assert(arm_imc_scf_begin_mode(2, 0) == ARM_IMC_MODE_SCF);
  assert(arm_imc_scf_begin_mode(1, 3) == ARM_IMC_MODE_SCF); /* SCF wins over dmc/imc */
  assert(arm_imc_scf_begin_mode(0, 3) == ARM_IMC_MODE_PERF);
  /* Nothing yet: stay enabled and re-scan (never disabled for the whole run). */
  assert(arm_imc_scf_begin_mode(0, 0) == ARM_IMC_MODE_RESCAN);
}

/* Two sockets with the given (per-boot) type numbers: sockets by name index, CPU by cpumask. */
static void check_two_sockets(int type0, int type1)
{
  struct arm_imc_scf s;

  reset_tree();
  add_scf_pmu(1, type1, "72\n");
  add_scf_pmu(0, type0, "0\n");
  arm_imc_scf_init(&s, g_dev, fake_perf_open);
  assert(arm_imc_scf_count_pmus(g_dev) == 2);
  assert(arm_imc_scf_attach(&s) == 2);
  assert(s.dev[0].socket == 0 && s.dev[0].cpu == 0);
  assert(s.dev[1].socket == 1 && s.dev[1].cpu == 72);
  assert(fake_perf_find((uint32_t)type0, 0x1a5, 0) >= 0);
  assert(fake_perf_find((uint32_t)type0, 0x1db, 0) >= 0);
  assert(fake_perf_find((uint32_t)type1, 0x1a5, 72) >= 0);
  assert(fake_perf_find((uint32_t)type1, 0x1db, 72) >= 0);
  assert(g_fake_perf_n == 4);
  arm_imc_scf_close(&s);
  assert(s.n == 0);
}

static void test_two_sockets_horizon_and_vista(void)
{
  check_two_sockets(25, 15); /* Horizon c122-018 */
  check_two_sockets(24, 16); /* Vista i618-141 */
}

static void test_read_scaled_cas(void)
{
  struct arm_imc_scf s;
  unsigned long long rd = 0, wr = 0;
  int ird, iwr;

  reset_tree();
  add_scf_pmu(0, 38, "0\n");
  arm_imc_scf_init(&s, g_dev, fake_perf_open);
  assert(arm_imc_scf_attach(&s) == 1);
  ird = fake_perf_find(38, 0x1a5, 0);
  iwr = fake_perf_find(38, 0x1db, 0);
  fake_perf_push(ird, 0, 0, 0);
  fake_perf_push(iwr, 0, 0, 0);
  assert(arm_imc_scf_read(&s.dev[0], &rd, &wr) == 0 && rd == 0 && wr == 0);
  /* 1000 beats running 1/3 of the time -> 3000 beats -> 1500 CAS; 6400 bytes -> 100 CAS. */
  fake_perf_push(ird, 1000, 300, 100);
  fake_perf_push(iwr, 6400, 300, 300);
  assert(arm_imc_scf_read(&s.dev[0], &rd, &wr) == 0);
  assert(rd == 1500 && wr == 100);
  /* No new sample this tick: the lifetime holds. */
  assert(arm_imc_scf_read(&s.dev[0], &rd, &wr) == 0 && rd == 1500 && wr == 100);
  assert(arm_imc_scf_read(NULL, &rd, &wr) == -1);
  assert(arm_imc_scf_read(&s.dev[0], NULL, &wr) == -1);
  arm_imc_scf_close(&s);
  assert(fake_perf_closed(ird) && fake_perf_closed(iwr));
}

static void test_single_socket_gh200(void)
{
  struct arm_imc_scf s;

  reset_tree();
  add_scf_pmu(0, 38, "0\n"); /* finding #11: one PMU, never assume two */
  arm_imc_scf_init(&s, g_dev, fake_perf_open);
  assert(arm_imc_scf_attach(&s) == 1);
  assert(s.dev[0].socket == 0 && s.dev[0].cpu == 0);
  arm_imc_scf_close(&s);
}

static void test_no_scf_pmu_rescans_then_attaches(void)
{
  struct arm_imc_scf s;
  int tick;

  /* GH200 before the F33 firmware: no SCF PMU, no error, caller falls back to dmc/imc. */
  reset_tree();
  arm_imc_scf_init(&s, g_dev, fake_perf_open);
  assert(arm_imc_scf_count_pmus(g_dev) == 0);
  assert(arm_imc_scf_attach(&s) == 0);
  assert(arm_imc_scf_begin_mode(s.n, 0) == ARM_IMC_MODE_RESCAN);
  /* O17: nvidia_cspmu binds later; the cheap re-scan picks it up on the Nth tick only. */
  for (tick = 1; tick < ARM_IMC_SCF_RESCAN_TICKS / 2; tick++)
    assert(arm_imc_scf_tick(&s) == 0);
  add_scf_pmu(0, 38, "0\n");
  for (; tick < ARM_IMC_SCF_RESCAN_TICKS; tick++)
    assert(arm_imc_scf_tick(&s) == 0);
  assert(g_fake_perf_n == 0); /* no open before the re-scan tick */
  assert(arm_imc_scf_tick(&s) == 1);
  assert(s.dev[0].socket == 0);
  assert(arm_imc_scf_tick(&s) == 1); /* attached: no more scans */
  assert(g_fake_perf_n == 2);
  arm_imc_scf_close(&s);
  assert(arm_imc_scf_tick(NULL) == 0);
}

static void test_bad_entries_and_open_failure_skip_socket(void)
{
  struct arm_imc_scf s;

  reset_tree();
  add_scf_pmu(0, 25, "0\n");
  add_scf_pmu(1, 15, "72\n");
  add_scf_pmu(2, 30, NULL);                              /* no cpumask: skipped */
  add_scf_pmu(3, 31, "garbage\n");                       /* unparsable cpumask: skipped */
  fake_sysfs_mkdir(g_root, "devices/nvidia_scf_pmu_x");  /* non-numeric: ignored */
  fake_sysfs_mkdir(g_root, "devices/nvidia_scf_pmu_99"); /* out of range: ignored */
  fake_perf_fail(0x1db, 72); /* socket 1's write counter cannot open (e.g. offline CPU) */
  arm_imc_scf_init(&s, g_dev, fake_perf_open);
  assert(arm_imc_scf_count_pmus(g_dev) == 4);
  assert(arm_imc_scf_attach(&s) == 1);
  assert(s.dev[0].socket == 0);
  /* Socket 1's read fd was opened then closed when the write failed. */
  assert(fake_perf_closed(fake_perf_find(15, 0x1a5, 72)));
  assert(!fake_perf_closed(fake_perf_find(25, 0x1a5, 0))); /* socket 0 stays open */
  arm_imc_scf_close(&s);
  /* Bad inputs. */
  assert(arm_imc_scf_count_pmus("/nonexistent/hpcperfstats") == 0);
  assert(arm_imc_scf_count_pmus(NULL) == 0);
  arm_imc_scf_init(&s, NULL, NULL);
  assert(arm_imc_scf_attach(&s) == 0);
  assert(arm_imc_scf_attach(NULL) == 0);
  arm_imc_scf_init(NULL, g_dev, NULL);
  arm_imc_scf_close(NULL);
}

int main(void)
{
  fake_sysfs_init(g_root, sizeof(g_root));
  test_cas_units();
  test_begin_mode_has_no_pending_state();
  test_two_sockets_horizon_and_vista();
  test_read_scaled_cas();
  test_single_socket_gh200();
  test_no_scf_pmu_rescans_then_attaches();
  test_bad_entries_and_open_failure_skip_socket();
  fake_sysfs_rm(g_root);
  fake_perf_reset();
  printf("test_arm_imc_scf: ok\n");
  return 0;
}
