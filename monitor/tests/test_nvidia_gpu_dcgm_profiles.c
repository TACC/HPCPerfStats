/* nvidia_gpu DCGM FieldGroupCreate profile contract (no live libdcgm). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "nvidia_gpu_dcgm_compat.h"
#include "nvidia_gpu_dcgm_profiles.h"

/* DCGM 1132 (SysIO) / 1133 (module) power left every profile; the meters are the per-socket
 * host_power_socket type, and the basic-no-board-power fallback for DCGM <= 3.1.x went with them. */
static void test_no_profile_watches_board_power(void)
{
  int p;

  for (p = 0; p < NVIDIA_GPU_WATCH_PROFILE_NR; p++) {
    assert(!nvidia_gpu_watch_profile_has_field(p, 1132));
    assert(!nvidia_gpu_watch_profile_has_field(p, 1133));
    assert(nvidia_gpu_watch_profile_has_field(p, DCGM_FI_DEV_POWER_USAGE));
    assert(nvidia_gpu_watch_profile_has_field(p, DCGM_FI_DEV_GPU_UTIL));
  }
}

static void test_profile_select_names_and_counts(void)
{
  const unsigned short *fid = NULL;
  unsigned int nf = 0;
  const char *name = NULL;

  assert(nvidia_gpu_watch_profile_select(0, &fid, &nf, &name) == 0);
  assert(nf == (unsigned int)NVIDIA_GPU_NFIELDS);
  assert(strcmp(name, "full-prof") == 0);

  assert(nvidia_gpu_watch_profile_select(1, &fid, &nf, &name) == 0);
  assert(nf == (unsigned int)NVIDIA_GPU_DCGM_NSPLIT_NO_DFMA);
  assert(strcmp(name, "split-prof-no-dfma") == 0);

  assert(nvidia_gpu_watch_profile_select(2, &fid, &nf, &name) == 0);
  assert(nf == (unsigned int)NVIDIA_GPU_DCGM_NCORE);
  assert(strcmp(name, "core-prof") == 0);

  assert(nvidia_gpu_watch_profile_select(3, &fid, &nf, &name) == 0);
  assert(nf == (unsigned int)NVIDIA_GPU_DCGM_NBASIC);
  assert(strcmp(name, "basic-nonprof") == 0);

  assert(nvidia_gpu_watch_profile_select(NVIDIA_GPU_WATCH_PROFILE_NR, &fid, &nf, &name) < 0);
  assert(nvidia_gpu_watch_profile_select(-1, &fid, &nf, &name) < 0);
  assert(nvidia_gpu_watch_profile_select(0, NULL, &nf, &name) < 0);
}

/* #116 A100 (DCGM 3.1.8): 1013/1014 supported, 1015 (DFMA) and 1016 (integer) not. full-prof
 * fails, so the rung after it must keep IMMA/HMMA and drop DFMA and the integer pipe, ahead of
 * core-prof (which drops 1013-1016). */
static void test_split_no_dfma_rung_before_core(void)
{
  int order[NVIDIA_GPU_WATCH_PROFILE_NR];
  const char *name = NULL;
  const unsigned short *fid = NULL;
  unsigned int nf = 0;
  int full_pos = -1;
  int split_pos = -1;
  int core_pos = -1;
  int i;
  int n = nvidia_gpu_watch_attempt_order(order, -1);

  for (i = 0; i < n; i++) {
    assert(nvidia_gpu_watch_profile_select(order[i], &fid, &nf, &name) == 0);
    if (strcmp(name, "full-prof") == 0)
      full_pos = i;
    else if (strcmp(name, "split-prof-no-dfma") == 0)
      split_pos = i;
    else if (strcmp(name, "core-prof") == 0)
      core_pos = i;
  }
  assert(full_pos >= 0 && split_pos >= 0 && core_pos >= 0);
  assert(full_pos < split_pos && split_pos < core_pos);
}

static void test_split_no_dfma_field_set_is_full_minus_dfma_and_integer_active(void)
{
  const unsigned short *full = NULL;
  const unsigned short *split = NULL;
  unsigned int nfull = 0;
  unsigned int nsplit = 0;
  const char *name = NULL;
  unsigned int i;

  assert(nvidia_gpu_watch_profile_select(0, &full, &nfull, &name) == 0);
  assert(nvidia_gpu_watch_profile_select(1, &split, &nsplit, &name) == 0);
  assert(strcmp(name, "split-prof-no-dfma") == 0);
  assert(nsplit + 2 == nfull);

  for (i = 0; i < nfull; i++) {
    if (full[i] == DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE || full[i] == DCGM_FI_PROF_PIPE_INT_ACTIVE)
      continue;
    assert(nvidia_gpu_watch_profile_has_field(1, full[i]));
  }
  for (i = 0; i < nsplit; i++)
    assert(nvidia_gpu_watch_profile_has_field(0, split[i]));

  assert(nvidia_gpu_watch_profile_has_field(1, DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE));
  assert(nvidia_gpu_watch_profile_has_field(1, DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE));
  assert(!nvidia_gpu_watch_profile_has_field(1, DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE));
  assert(nvidia_gpu_watch_profile_has_field(1, DCGM_FI_PROF_PIPE_TENSOR_ACTIVE));
  assert(nvidia_gpu_watch_profile_has_field(0, DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE));
  assert(!nvidia_gpu_watch_profile_has_field(2, DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE));
}

/* integer_active (DCGM 1016) is watched by full-prof only: split-prof-no-dfma (A100 / DCGM 3.1.8)
 * and core-prof drop it, core-prof drops every tensor split field 1013-1016, basic drops PROF. */
static void test_integer_active_1016_ladder(void)
{
  static const unsigned short split[] = {
      DCGM_FI_PROF_PIPE_TENSOR_IMMA_ACTIVE, DCGM_FI_PROF_PIPE_TENSOR_HMMA_ACTIVE,
      DCGM_FI_PROF_PIPE_TENSOR_DFMA_ACTIVE, DCGM_FI_PROF_PIPE_INT_ACTIVE};
  size_t i;

  assert(DCGM_FI_PROF_PIPE_INT_ACTIVE == 1016);
  assert(nvidia_gpu_watch_profile_has_field(0, DCGM_FI_PROF_PIPE_INT_ACTIVE));
  assert(!nvidia_gpu_watch_profile_has_field(1, DCGM_FI_PROF_PIPE_INT_ACTIVE));
  for (i = 0; i < sizeof(split) / sizeof(split[0]); i++) {
    assert(nvidia_gpu_watch_profile_has_field(0, split[i]));
    assert(!nvidia_gpu_watch_profile_has_field(2, split[i]));
    assert(!nvidia_gpu_watch_profile_has_field(3, split[i]));
  }
  assert(NVIDIA_GPU_NFIELDS == 25);
  assert(NVIDIA_GPU_DCGM_NSPLIT_NO_DFMA == NVIDIA_GPU_NFIELDS - 2);
  assert(NVIDIA_GPU_DCGM_NCORE == NVIDIA_GPU_NFIELDS - 4);
}

/* After the A100 lands on split-prof-no-dfma, the next begin / SIGHUP starts from it. */
static void test_split_no_dfma_sticky_last_profile(void)
{
  int order[NVIDIA_GPU_WATCH_PROFILE_NR];
  int n = nvidia_gpu_watch_attempt_order(order, 1);

  assert(n == NVIDIA_GPU_WATCH_PROFILE_NR);
  assert(order[0] == 1);
  assert(order[1] == 0 && order[2] == 2 && order[3] == 3);
}

static void test_attempt_order_default_and_sticky(void)
{
  int order[NVIDIA_GPU_WATCH_PROFILE_NR];
  int n;
  int i;
  int seen[NVIDIA_GPU_WATCH_PROFILE_NR];

  n = nvidia_gpu_watch_attempt_order(order, -1);
  assert(n == NVIDIA_GPU_WATCH_PROFILE_NR);
  for (i = 0; i < NVIDIA_GPU_WATCH_PROFILE_NR; i++)
    assert(order[i] == i);

  n = nvidia_gpu_watch_attempt_order(order, 3);
  assert(n == NVIDIA_GPU_WATCH_PROFILE_NR);
  assert(order[0] == 3);
  memset(seen, 0, sizeof(seen));
  for (i = 0; i < n; i++) {
    assert(order[i] >= 0 && order[i] < NVIDIA_GPU_WATCH_PROFILE_NR);
    assert(!seen[order[i]]);
    seen[order[i]] = 1;
  }

  n = nvidia_gpu_watch_attempt_order(order, 1);
  assert(n == NVIDIA_GPU_WATCH_PROFILE_NR);
  assert(order[0] == 1);
  assert(nvidia_gpu_watch_attempt_order(NULL, 0) == 0);
}

int main(void)
{
  test_no_profile_watches_board_power();
  test_profile_select_names_and_counts();
  test_attempt_order_default_and_sticky();
  test_split_no_dfma_rung_before_core();
  test_split_no_dfma_field_set_is_full_minus_dfma_and_integer_active();
  test_integer_active_1016_ladder();
  test_split_no_dfma_sticky_last_profile();
  printf("test_nvidia_gpu_dcgm_profiles: OK\n");
  return 0;
}
