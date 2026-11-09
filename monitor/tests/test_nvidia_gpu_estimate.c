/* nvidia_gpu_estimate_rates / nvidia_gpu_link_rate_integrate. */
#include <assert.h>
#include <limits.h>
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

static void test_link_rate_integrate_accumulates(void)
{
  unsigned long long acc = 0ULL;

  /* c122-018 idle PCIe TX 175 B/s over a 60 s collect = 10500 B. */
  assert(nvidia_gpu_link_rate_integrate(175ULL, 60000000LL, &acc) == 10500ULL);
  /* The rate dropped (175 -> 157) but the counter still grows (was "decreased"). */
  assert(nvidia_gpu_link_rate_integrate(157ULL, 60000000LL, &acc) == 9420ULL);
  assert(acc == 19920ULL);
  /* 2.653e11 B/s NvLink for 0.5 s. */
  acc = 0ULL;
  assert(nvidia_gpu_link_rate_integrate(265333528373ULL, 500000LL, &acc) == 132666764187ULL);
}

static void test_link_rate_integrate_first_collect_and_null(void)
{
  unsigned long long acc = 42ULL;

  assert(nvidia_gpu_link_rate_integrate(1000ULL, 0LL, &acc) == 0ULL);
  assert(nvidia_gpu_link_rate_integrate(1000ULL, -5LL, &acc) == 0ULL);
  assert(acc == 42ULL);
  assert(nvidia_gpu_link_rate_integrate(1000ULL, 1000000LL, NULL) == 0ULL);
}

static void test_link_rate_integrate_saturates(void)
{
  unsigned long long acc = ULLONG_MAX - 10ULL;

  assert(nvidia_gpu_link_rate_integrate(100ULL, 1000000LL, &acc) == 100ULL);
  assert(acc == ULLONG_MAX);
  acc = 0ULL;
  assert(nvidia_gpu_link_rate_integrate(UINT64_MAX, 3600000000LL, &acc) == ULLONG_MAX);
  assert(acc == ULLONG_MAX);
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
  test_link_rate_integrate_accumulates();
  test_link_rate_integrate_first_collect_and_null();
  test_link_rate_integrate_saturates();
  printf("test_nvidia_gpu_estimate passed\n");
  return 0;
}
