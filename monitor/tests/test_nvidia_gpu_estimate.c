/* nvidia_gpu_estimate_rates: approximate FLOP and memory-bandwidth rates. */
#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "nvidia_gpu_estimate.h"

static void test_null_input_zeroes_outputs(void)
{
  double flops = 99.0;
  double mem_bw = 99.0;

  nvidia_gpu_estimate_rates(NULL, &flops, &mem_bw);
  assert(flops == 0.0);
  assert(mem_bw == 0.0);
}

static void test_fp_mix_clamped_and_scaled(void)
{
  struct nvidia_gpu_estimate_input in = {
    .fp64_active = 0.25,
    .fp32_active = 0.25,
    .fp16_active = 0.0,
    .tensor_active = 0.0,
    .mem_util = 0.0,
  };
  double flops;
  double mem_bw;

  nvidia_gpu_estimate_rates(&in, &flops, &mem_bw);
  assert(fabs(flops - 0.5 * 60000000000000.0) < 1.0);
  assert(mem_bw == 0.0);
}

static void test_mem_util_scales_bandwidth(void)
{
  struct nvidia_gpu_estimate_input in = {
    .fp64_active = 0.0,
    .fp32_active = 0.0,
    .fp16_active = 0.0,
    .tensor_active = 0.0,
    .mem_util = 50.0,
  };
  double flops;
  double mem_bw;

  nvidia_gpu_estimate_rates(&in, &flops, &mem_bw);
  assert(flops == 0.0);
  assert(fabs(mem_bw - 0.5 * 1000000000000.0) < 1.0);
}

static void test_link_u64_delta_monotonic(void)
{
  uint64_t prev = 100ULL;

  assert(nvidia_gpu_link_u64_delta(250ULL, &prev) == 150ULL);
  assert(prev == 250ULL);
  assert(nvidia_gpu_link_u64_delta(300ULL, &prev) == 50ULL);
}

static void test_link_u64_delta_reset(void)
{
  uint64_t prev = 500ULL;

  assert(nvidia_gpu_link_u64_delta(100ULL, &prev) == 100ULL);
  assert(prev == 100ULL);
}

static void test_link_u64_delta_null_prev(void)
{
  assert(nvidia_gpu_link_u64_delta(100ULL, NULL) == 0ULL);
}

static void test_over_one_mix_clamped(void)
{
  struct nvidia_gpu_estimate_input in = {
    .fp64_active = 2.0,
    .fp32_active = 0.0,
    .fp16_active = 0.0,
    .tensor_active = 0.0,
    .mem_util = -5.0,
  };
  double flops;
  double mem_bw;

  nvidia_gpu_estimate_rates(&in, &flops, &mem_bw);
  assert(fabs(flops - 60000000000000.0) < 1.0);
  assert(mem_bw == 0.0);
}

int main(void)
{
  test_null_input_zeroes_outputs();
  test_fp_mix_clamped_and_scaled();
  test_mem_util_scales_bandwidth();
  test_over_one_mix_clamped();
  test_link_u64_delta_monotonic();
  test_link_u64_delta_reset();
  test_link_u64_delta_null_prev();
  printf("test_nvidia_gpu_estimate passed\n");
  return 0;
}
