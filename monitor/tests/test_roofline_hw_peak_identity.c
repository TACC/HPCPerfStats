/* Unit tests for roofline GPU/CPU identity tables and nvidia-smi CSV parsers. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "cpuid.h"
#include "roofline_hw_peak_identity.h"

static void test_gb200_sm_and_hbm(void)
{
  assert(roofline_nvidia_sm_count_from_name("NVIDIA GB200") == ROOFLINE_GB200_SM_COUNT);
  assert(roofline_nvidia_sm_count_from_name("Tesla V100") == 0);
  assert(roofline_nvidia_hbm_bw_from_name("NVIDIA GB200") == ROOFLINE_GB200_HBM_BW_BYTES_PER_S);
  assert(roofline_nvidia_hbm_bw_from_name("Unknown GPU") == 0.0);
}

static void test_gh200_sm_hbm_c2c(void)
{
  const char *name = "NVIDIA GH200 120GB";

  assert(roofline_nvidia_sm_count_from_name(name) == ROOFLINE_GH200_SM_COUNT);
  assert(roofline_nvidia_hbm_bw_from_name_mem(name, 97871.0) == ROOFLINE_GH200_HBM_BW_BYTES_PER_S);
  assert(roofline_nvidia_hbm_bw_from_name_mem(name, 150000.0) ==
         ROOFLINE_GH200_HBM3E_BW_BYTES_PER_S);
  assert(roofline_nvidia_c2c_bw_from_name(name) == ROOFLINE_GH200_C2C_BW_BYTES_PER_S);
  assert(roofline_nvidia_c2c_bw_from_name("NVIDIA GB200") == 0.0);
}

static void test_s3_ls6_gpu_identity(void)
{
  const char *h100 = "NVIDIA H100";
  const char *a100 = "NVIDIA A100-PCIE-40GB";
  const char *rtx = "NVIDIA RTX PRO 6000 Blackwell Server Edition";

  assert(roofline_nvidia_sm_count_from_name(h100) == ROOFLINE_H100_SM_COUNT);
  assert(roofline_nvidia_hbm_bw_from_name(h100) == ROOFLINE_H100_HBM_BW_BYTES_PER_S);
  assert(roofline_nvidia_sm_count_from_name(a100) == ROOFLINE_A100_40_SM_COUNT);
  assert(roofline_nvidia_hbm_bw_from_name(a100) == ROOFLINE_A100_40_HBM_BW_BYTES_PER_S);
  assert(roofline_nvidia_sm_count_from_name(rtx) == ROOFLINE_RTX_PRO_6000_SM_COUNT);
  assert(roofline_nvidia_hbm_bw_from_name(rtx) == ROOFLINE_RTX_PRO_6000_GDDR_BW_BYTES_PER_S);
  assert(roofline_nvidia_sm_count_from_name("NVIDIA GB202GL") == ROOFLINE_RTX_PRO_6000_SM_COUNT);
  /* Match order: GH200 before H100 substring; RTX before H100. */
  assert(roofline_nvidia_sm_count_from_name("NVIDIA GH200") == ROOFLINE_GH200_SM_COUNT);
  assert(fabs(roofline_nvidia_fp64_ratio_from_cc(12, 0, rtx) - (1.0 / 64.0)) < 1e-12);
}

static void test_grace_dram(void)
{
  assert(roofline_grace_dram_bw_from_cpu_part(0xd4f) == ROOFLINE_GRACE_DRAM_BW_BYTES_PER_S);
  assert(roofline_grace_dram_bw_from_cpu_part(0xd40) == 0.0);
}

static void test_cpu_dram_cpuid_identity(void)
{
  double ddr = 0.0, hbm = 0.0;

  assert(roofline_cpu_mem_bw_from_processor(SKYLAKE_X, NULL, &ddr, &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_SKYLAKE_X_DRAM_BW_BYTES_PER_S) < 1.0);
  assert(hbm == 0.0);

  assert(roofline_cpu_mem_bw_from_processor(CASCADE_LAKE, NULL, &ddr, &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_CASCADE_LAKE_DRAM_BW_BYTES_PER_S) < 1.0);

  assert(roofline_cpu_mem_bw_from_processor(ICELAKE_SERVER, NULL, &ddr, &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_ICELAKE_SERVER_DRAM_BW_BYTES_PER_S) < 1.0);

  assert(roofline_cpu_mem_bw_from_processor(SAPPHIRE_RAPIDS, "Intel(R) Xeon(R) Platinum 8468", &ddr,
                                            &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_SAPPHIRE_RAPIDS_DDR_BW_BYTES_PER_S) < 1.0);
  assert(hbm == 0.0);

  assert(roofline_cpu_mem_bw_from_processor(SAPPHIRE_RAPIDS, "Intel(R) Xeon(R) CPU Max 9480", &ddr,
                                            &hbm) == 0);
  assert(ddr == 0.0);
  assert(fabs(hbm - ROOFLINE_SAPPHIRE_RAPIDS_MAX_HBM_BW_BYTES_PER_S) < 1.0);

  assert(roofline_cpu_mem_bw_from_processor(AMD_MILAN, NULL, &ddr, &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_AMD_MILAN_DRAM_BW_BYTES_PER_S) < 1.0);

  assert(roofline_cpu_mem_bw_from_processor(AMD_GENOA, NULL, &ddr, &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_AMD_GENOA_DRAM_BW_BYTES_PER_S) < 1.0);

  assert(roofline_cpu_mem_bw_from_processor(AMD_TURIN, NULL, &ddr, &hbm) == 0);
  assert(fabs(ddr - ROOFLINE_AMD_TURIN_DRAM_BW_BYTES_PER_S) < 1.0);

  assert(roofline_cpu_mem_bw_from_processor(NEHALEM, NULL, &ddr, &hbm) != 0);
  assert(ddr == 0.0 && hbm == 0.0);
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

static void test_parse_smi_flops_vista_gh200(void)
{
  char name[64];
  double sm_mhz = 0.0;
  int maj = 0, min = 0;
  int sm;
  double flops;

  assert(roofline_parse_smi_flops_line("NVIDIA GH200 120GB, 1980, 9.0", name, sizeof(name), &sm_mhz,
                                       &maj, &min) == 0);
  assert(strcmp(name, "NVIDIA GH200 120GB") == 0);
  assert(sm_mhz == 1980.0);
  assert(maj == 9 && min == 0);
  sm = roofline_nvidia_sm_count_from_name(name);
  assert(sm == ROOFLINE_GH200_SM_COUNT);
  flops = roofline_nvidia_fp64_flops_from_sm(sm, roofline_nvidia_cuda_cores_per_sm(maj, min, name),
                                             sm_mhz,
                                             roofline_nvidia_fp64_ratio_from_cc(maj, min, name));
  assert(fabs(flops - 33454080000000.0) < 1.0);
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

static void test_parse_smi_mem_pcie_vista_gh200(void)
{
  char name[64];
  double mem_mib = 0.0, mem_mhz = 0.0;
  int gen = 0, width = 0;
  double pcie_io;
  double c2c;

  assert(roofline_parse_smi_mem_pcie_line("NVIDIA GH200 120GB, 97871, 2619, 4, 1", name,
                                          sizeof(name), &mem_mib, &mem_mhz, &gen, &width) == 0);
  assert(strcmp(name, "NVIDIA GH200 120GB") == 0);
  assert(fabs(mem_mib - 97871.0) < 0.5);
  assert(gen == 4 && width == 1);
  pcie_io = roofline_pcie_gen_lane_bytes_per_s(gen) * (double)width;
  c2c = roofline_nvidia_c2c_bw_from_name(name);
  assert(c2c > pcie_io);
  assert(fabs(roofline_nvidia_hbm_bw_from_name_mem(name, mem_mib) -
              ROOFLINE_GH200_HBM_BW_BYTES_PER_S) < 1.0);
}

static void test_unknown_gpu_no_invent(void)
{
  assert(roofline_nvidia_hbm_bw_from_name("NVIDIA GeForce RTX 4090") == 0.0);
  assert(roofline_nvidia_sm_count_from_name("NVIDIA GeForce RTX 4090") == 0);
  assert(roofline_nvidia_c2c_bw_from_name("NVIDIA GeForce RTX 4090") == 0.0);
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
  test_gh200_sm_hbm_c2c();
  test_s3_ls6_gpu_identity();
  test_grace_dram();
  test_cpu_dram_cpuid_identity();
  test_parse_smi_flops_horizon();
  test_parse_smi_flops_vista_gh200();
  test_parse_smi_mem_pcie_horizon();
  test_parse_smi_mem_pcie_vista_gh200();
  test_unknown_gpu_no_invent();
  test_reject_invalid_flops_line();
  printf("test_roofline_hw_peak_identity passed\n");
  return 0;
}
