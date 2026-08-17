/* nvidia_gpu DCGM FieldGroupCreate profile contract (no live libdcgm). */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "nvidia_gpu_dcgm_compat.h"
#include "nvidia_gpu_dcgm_profiles.h"

static void test_preferred_profiles_keep_board_power(void)
{
  int p;
  for (p = 0; p <= 2; p++) {
    assert(nvidia_gpu_watch_profile_has_field(p, DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT));
    assert(nvidia_gpu_watch_profile_has_field(p, DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT));
  }
}

static void test_last_resort_omits_board_power(void)
{
  assert(!nvidia_gpu_watch_profile_has_field(3, DCGM_FI_DEV_SYSIO_POWER_UTIL_CURRENT));
  assert(!nvidia_gpu_watch_profile_has_field(3, DCGM_FI_DEV_MODULE_POWER_UTIL_CURRENT));
  assert(nvidia_gpu_watch_profile_has_field(3, DCGM_FI_DEV_POWER_USAGE));
  assert(nvidia_gpu_watch_profile_has_field(3, DCGM_FI_DEV_GPU_UTIL));
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
  assert(nf == (unsigned int)NVIDIA_GPU_DCGM_NCORE);
  assert(strcmp(name, "core-prof") == 0);

  assert(nvidia_gpu_watch_profile_select(2, &fid, &nf, &name) == 0);
  assert(nf == (unsigned int)NVIDIA_GPU_DCGM_NBASIC);
  assert(strcmp(name, "basic-nonprof") == 0);

  assert(nvidia_gpu_watch_profile_select(3, &fid, &nf, &name) == 0);
  assert(nf == (unsigned int)NVIDIA_GPU_DCGM_NBASIC_NO_BOARD_POWER);
  assert(strcmp(name, "basic-no-board-power") == 0);

  assert(nvidia_gpu_watch_profile_select(4, &fid, &nf, &name) < 0);
  assert(nvidia_gpu_watch_profile_select(0, NULL, &nf, &name) < 0);
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
  test_preferred_profiles_keep_board_power();
  test_last_resort_omits_board_power();
  test_profile_select_names_and_counts();
  test_attempt_order_default_and_sticky();
  printf("test_nvidia_gpu_dcgm_profiles: OK\n");
  return 0;
}
