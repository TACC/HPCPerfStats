/* Unit tests for roofline GPU/CPU identity tables and nvidia-smi CSV parsers. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "roofline_hw_peak_identity.h"

static void test_gb200_sm_and_hbm(void)
{
  assert(roofline_nvidia_sm_count_from_name("NVIDIA GB200") == ROOFLINE_GB200_SM_COUNT);
  assert(roofline_nvidia_sm_count_from_name("Tesla V100") == 0);
  assert(roofline_nvidia_hbm_bw_from_name("NVIDIA GB200") == ROOFLINE_GB200_HBM_BW_BYTES_PER_S);
  assert(roofline_nvidia_hbm_bw_from_name("Unknown GPU") == 0.0);
}

static void test_grace_dram(void)
{
  assert(roofline_grace_dram_bw_from_cpu_part(0xd4f) == ROOFLINE_GRACE_DRAM_BW_BYTES_PER_S);
  assert(roofline_grace_dram_bw_from_cpu_part(0xd40) == 0.0);
}

static void test_parse_smi_flops_horizon(void)
{
  char name[64];
  double sm_mhz = 0.0;
  int maj = 0, min = 0;
  int sm;
  double flops;

  assert(roofline_parse_smi_flops_line("NVIDIA GB200, 2062, 10.0", name, sizeof(name), &sm_mhz,
                                       &maj, &min) == 0);
  assert(strcmp(name, "NVIDIA GB200") == 0);
  assert(sm_mhz == 2062.0);
  assert(maj == 10 && min == 0);
  sm = roofline_nvidia_sm_count_from_name(name);
  flops = roofline_nvidia_fp64_flops_from_sm(sm, roofline_nvidia_cuda_cores_per_sm(maj, min, name),
                                             sm_mhz,
                                             roofline_nvidia_fp64_ratio_from_cc(maj, min, name));
  assert(flops > 0.0);
}

static void test_parse_smi_mem_pcie_horizon(void)
{
  char name[64];
  double mem_mib = 0.0, mem_mhz = 0.0;
  int gen = 0, width = 0;
  double io;
  double mem;

  assert(roofline_parse_smi_mem_pcie_line("NVIDIA GB200, 189471, 3996, 4, 16", name, sizeof(name),
                                          &mem_mib, &mem_mhz, &gen, &width) == 0);
  assert(strcmp(name, "NVIDIA GB200") == 0);
  assert(gen == 4 && width == 16);
  io = roofline_pcie_gen_lane_bytes_per_s(gen) * (double)width;
  assert(io > 0.0);
  mem = 4.0 * roofline_nvidia_hbm_bw_from_name(name);
  assert(fabs(mem - 4.0 * ROOFLINE_GB200_HBM_BW_BYTES_PER_S) < 1.0);
}

static void test_unknown_gpu_no_invent(void)
{
  assert(roofline_nvidia_hbm_bw_from_name("NVIDIA GeForce RTX 4090") == 0.0);
  assert(roofline_nvidia_sm_count_from_name("NVIDIA GeForce RTX 4090") == 0);
}

static void test_reject_invalid_flops_line(void)
{
  char name[64];
  double sm_mhz = 0.0;
  int maj = 0, min = 0;

  assert(roofline_parse_smi_flops_line("NVIDIA GB200, 2062", name, sizeof(name), &sm_mhz, &maj,
                                       &min) != 0);
}

int main(void)
{
  test_gb200_sm_and_hbm();
  test_grace_dram();
  test_parse_smi_flops_horizon();
  test_parse_smi_mem_pcie_horizon();
  test_unknown_gpu_no_invent();
  test_reject_invalid_flops_line();
  printf("test_roofline_hw_peak_identity passed\n");
  return 0;
}
