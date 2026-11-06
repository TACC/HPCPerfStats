/* NVML / GPM converters (round 2 J2). Values from finding 21 (GB200 GPU 0 under
 * dcgmproftester): GPM percent = DCGM ratio x 100; GPM MiB/s x 1048576 = DCGM bytes/s. */
#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

#include "nvidia_gpu_nvml_convert.h"

static int near(double a, double b)
{
  return fabs(a - b) < 1e-9;
}

static void test_pct_to_ratio_finding21(void)
{
  double r = -1.0;

  assert(nvidia_gpu_nvml_pct_to_ratio(95.44, &r) == 0 && near(r, 0.9544)); /* SM active */
  assert(nvidia_gpu_nvml_pct_to_ratio(99.73, &r) == 0 && near(r, 0.9973)); /* occupancy */
  assert(nvidia_gpu_nvml_pct_to_ratio(87.47, &r) == 0 && near(r, 0.8747)); /* tensor */
  assert(nvidia_gpu_nvml_pct_to_ratio(80.39, &r) == 0 && near(r, 0.8039)); /* DRAM */
  assert(nvidia_gpu_nvml_pct_to_ratio(94.05, &r) == 0 && near(r, 0.9405)); /* FP64 / DFMA */
  assert(nvidia_gpu_nvml_pct_to_ratio(0.0, &r) == 0 && r == 0.0);          /* IMMA idle (D17) */
  assert(nvidia_gpu_nvml_pct_to_ratio(100.0, &r) == 0 && r == 1.0);
}

static void test_pct_to_ratio_bounds(void)
{
  double r = 0.25;

  /* Rounding slack is clamped; real garbage leaves out unchanged. */
  assert(nvidia_gpu_nvml_pct_to_ratio(100.3, &r) == 0 && r == 1.0);
  assert(nvidia_gpu_nvml_pct_to_ratio(-0.2, &r) == 0 && r == 0.0);
  r = 0.25;
  assert(nvidia_gpu_nvml_pct_to_ratio(250.0, &r) == -1 && r == 0.25);
  assert(nvidia_gpu_nvml_pct_to_ratio(-5.0, &r) == -1 && r == 0.25);
  assert(nvidia_gpu_nvml_pct_to_ratio(NAN, &r) == -1 && r == 0.25);
  assert(nvidia_gpu_nvml_pct_to_ratio(INFINITY, &r) == -1 && r == 0.25);
  assert(nvidia_gpu_nvml_pct_to_ratio(50.0, NULL) == -1);
}

static void test_mib_per_s_to_bytes(void)
{
  /* Idle PCIe TX 0.7541 MiB/s = 790,731 B/s (DCGM read 806,939 in a shifted 10 s window). */
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(0.7541) == 790731ULL);
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(1.0) == 1048576ULL);
  /* NvLink TX 2.53e5 MiB/s -> 2.653e11 B/s. */
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(253000.0) == 265289728000ULL);
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(0.0) == 0);
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(-3.0) == 0);
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(NAN) == 0);
  assert(nvidia_gpu_nvml_mib_per_s_to_bytes(1e300) == UINT64_MAX);
}

static void test_power_and_memory(void)
{
  assert(near(nvidia_gpu_nvml_mw_to_w(1169000U), 1169.0));
  assert(near(nvidia_gpu_nvml_mw_to_w(151250U), 151.25));
  assert(nvidia_gpu_nvml_mw_to_w(0U) == 0.0);
  assert(nvidia_gpu_nvml_bytes_to_mib(0ULL) == 0);
  assert(nvidia_gpu_nvml_bytes_to_mib(1048576ULL) == 1);
  assert(nvidia_gpu_nvml_bytes_to_mib(1048575ULL) == 0);
  assert(nvidia_gpu_nvml_bytes_to_mib(193273528320ULL) == 184320); /* 180 GiB */
}

int main(void)
{
  test_pct_to_ratio_finding21();
  test_pct_to_ratio_bounds();
  test_mib_per_s_to_bytes();
  test_power_and_memory();
  printf("test_nvidia_gpu_nvml_convert passed\n");
  return 0;
}
