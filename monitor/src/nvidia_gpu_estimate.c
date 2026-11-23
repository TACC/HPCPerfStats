#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "nvidia_gpu_estimate.h"

#define NVIDIA_GPU_NELEM(a) (sizeof(a) / sizeof((a)[0]))

/* Duty-cycle key and row field of each enum nvidia_gpu_pipe. */
static const struct {
  const char *key;
  size_t offset; /* double in dcgm_data_t */
} g_pipes[NVIDIA_GPU_PIPE_NR] = {
    {"fp64_active", offsetof(dcgm_data_t, fp64_active)},
    {"fp32_active", offsetof(dcgm_data_t, fp32_active)},
    {"fp16_active", offsetof(dcgm_data_t, fp16_active)},
    {"tensor_hmma_active", offsetof(dcgm_data_t, tensor_hmma_active)},
    {"tensor_dmma_active", offsetof(dcgm_data_t, tensor_dmma_active)},
};

/*
 * FLOPs per SM per clock (one FMA = 2 FLOPs) by compute capability, in enum nvidia_gpu_pipe order.
 *
 * Scalar pipes (fp64 / fp32 / fp16): NVIDIA CUDA C++ Programming Guide 12.9, table "Throughput of
 * Native Arithmetic Instructions" (results per clock per multiprocessor) x 2:
 *   16-bit add/mul/fma 256 on 8.0, 9.0, 10.0, 12.0; 32-bit 64 on 8.0, 128 on 9.0 / 10.0 / 12.0;
 *   64-bit 32 on 8.0, 64 on 9.0 / 10.0, 2 on 12.0. The 32-bit figure is also the FP32 lanes per
 *   SM (fp32_lanes).
 * Tensor HMMA (dense FP16) / DMMA (FP64 tensor):
 *   8.0 NVIDIA A100 Tensor Core GPU Architecture whitepaper (GA100): 312 TFLOPS dense FP16 tensor
 *       and 19.5 TFLOPS FP64 tensor at 108 SMs x 1410 MHz -> 2048 and 128.
 *   9.0 NVIDIA H100 Tensor Core GPU Architecture whitepaper: "On a per SM basis, the Tensor Cores
 *       deliver 2x the MMA computational rates of the A100 SM on equivalent data types" -> 4096
 *       and 256 (H100 SXM5 datasheet: 989.4 TFLOPS dense FP16 at 1830 MHz, 66.9 TFLOPS FP64
 *       tensor at 1980 MHz, 132 SMs). GH200 carries the same H100 GPU.
 *   Blackwell datasheets give per-GPU tensor peaks but neither a per-SM rate nor the clock behind
 *   them, so Blackwell tensor values are measured or 0 (not counted):
 *   12.0 (RTX PRO 6000 Blackwell, GB202) HMMA measured 2026-11-23 on Stampede3 c573-002 (188 SMs,
 *       driver 615.71.09, cuBLAS 13.2): measurement/bin/gpu_tensor_calibrate.sh FP16 GEMM FLOP/s /
 *       (tensor_hmma_active x SMs x sm_clock) from the monitor's own rows gave 1022.7 (n=8192)
 *       and 1030.7 (n=16384) -> 1024. DMMA 0: tensor_dmma_active stays 0 under DGEMM (no FP64
 *       tensor path).
 *   10.0 (B200 / GB200) tensor pipes stay 0 until calibrated on Horizon GB200.
 * tests/test_nvidia_gpu_estimate.c (peak_table) checks each value, and its per-GPU product
 * against the published peak where one exists.
 */
static const struct {
  unsigned char major, minor;
  unsigned char fp32_lanes;
  unsigned short flops[NVIDIA_GPU_PIPE_NR];
} g_peaks[] = {
    {8, 0, 64, {64, 128, 512, 2048, 128}},
    {9, 0, 128, {128, 256, 512, 4096, 256}},
    {10, 0, 128, {128, 256, 512, 0, 0}},
    {12, 0, 128, {4, 256, 512, 1024, 0}},
};

static const char *const g_mem_inputs[] = {"gpu_mem_util"};
/* Rate inputs and, under the same names, the integrated link E keys. */
static const char *const g_link_keys[] = {"gpu_pcie_tx_bytes", "gpu_pcie_rx_bytes",
                                          "gpu_nvlink_tx_bytes", "gpu_nvlink_rx_bytes"};

enum { EST_FLOPS, EST_MEM, EST_LINK, EST_NGROUPS };
static const struct {
  const char *key;
  unsigned char group;   /* EST_* */
  unsigned char counter; /* cumulative E key: code through nvidia_gpu_estimate_counter_code */
} g_derived[NVIDIA_GPU_ESTIMATE_DERIVED_KEYS] = {
    {"gpu_flops_rate", EST_FLOPS, 0},         {"gpu_flops", EST_FLOPS, 1},
    {"gpu_mem_bw_bytes_rate", EST_MEM, 0},    {"gpu_mem_read_bytes", EST_MEM, 1},
    {"gpu_mem_write_bytes", EST_MEM, 1},      {"gpu_mem_total_bytes", EST_MEM, 1},
    {"gpu_io_link_total_bytes", EST_LINK, 1},
};

static int key_in(const char *key, const char *const *keys, size_t n)
{
  for (size_t i = 0; i < n; i++)
    if (strcmp(key, keys[i]) == 0)
      return 1;
  return 0;
}

static enum stats_sentinel group_code(const dcgm_data_t *row, const char *const *keys, size_t n)
{
  for (int k = 0; k < row->nmiss; k++)
    if (key_in(row->miss[k].key, keys, n))
      return (enum stats_sentinel)row->miss[k].code;
  return STATS_SENTINEL_OK;
}

static int peak_index(int major, int minor)
{
  for (size_t i = 0; i < NVIDIA_GPU_NELEM(g_peaks); i++)
    if (g_peaks[i].major == major && g_peaks[i].minor == minor)
      return (int)i;
  return -1;
}

double nvidia_gpu_flops_per_sm_clock(int major, int minor, enum nvidia_gpu_pipe pipe)
{
  int i = peak_index(major, minor);

  if (i < 0 || pipe < 0 || pipe >= NVIDIA_GPU_PIPE_NR)
    return 0.0;
  return (double)g_peaks[i].flops[pipe];
}

unsigned int nvidia_gpu_sm_count(const dcgm_data_t *row)
{
  int i;

  if (row == NULL)
    return 0;
  if (row->sm_count > 0)
    return row->sm_count;
  i = peak_index(row->cc_major, row->cc_minor);
  return i < 0 ? 0 : row->gpu_cores / g_peaks[i].fp32_lanes;
}

static enum stats_sentinel flops_code(const dcgm_data_t *row)
{
  int i = peak_index(row->cc_major, row->cc_minor);
  const char *clock = "gpu_sm_clock";
  enum stats_sentinel c;
  int counted = 0;

  if (i < 0 || nvidia_gpu_sm_count(row) == 0)
    return STATS_SENTINEL_NOT_PRESENT;
  c = group_code(row, &clock, 1);
  if (c != STATS_SENTINEL_OK)
    return c;
  for (int p = 0; p < NVIDIA_GPU_PIPE_NR; p++) {
    if (g_peaks[i].flops[p] == 0)
      continue;
    c = group_code(row, &g_pipes[p].key, 1);
    if (c == STATS_SENTINEL_NOT_PRESENT)
      continue;
    if (c != STATS_SENTINEL_OK)
      return c;
    counted = 1;
  }
  return counted ? STATS_SENTINEL_OK : STATS_SENTINEL_NOT_PRESENT;
}

void nvidia_gpu_estimate_row_codes(const dcgm_data_t *row, struct nvidia_gpu_estimate_codes *out)
{
  if (out == NULL)
    return;
  if (row == NULL) {
    out->flops = out->mem = out->link = STATS_SENTINEL_NOT_PRESENT;
    return;
  }
  out->flops = flops_code(row);
  out->mem = group_code(row, g_mem_inputs, NVIDIA_GPU_NELEM(g_mem_inputs));
  out->link = group_code(row, g_link_keys, NVIDIA_GPU_NELEM(g_link_keys));
}

enum stats_sentinel nvidia_gpu_estimate_counter_code(enum stats_sentinel input)
{
  switch (input) {
  case STATS_SENTINEL_NOT_UPDATED:
  case STATS_SENTINEL_READ_FAILED:
  case STATS_SENTINEL_BACKEND_ERROR:
  case STATS_SENTINEL_INVALID_VALUE:
  case STATS_SENTINEL_BACKOFF:
    return STATS_SENTINEL_NOT_SCHEDULED;
  default:
    return input;
  }
}

int nvidia_gpu_estimate_miss_list(const dcgm_data_t *row,
                                  const struct nvidia_gpu_estimate_codes *codes,
                                  struct nvidia_gpu_row_miss out[NVIDIA_GPU_ESTIMATE_MISS_MAX])
{
  enum stats_sentinel group[EST_NGROUPS];
  int n = 0;

  if (row == NULL || codes == NULL || out == NULL)
    return 0;
  for (int k = 0; k < row->nmiss && k < NVIDIA_GPU_ROW_MISS_MAX; k++) {
    out[n] = row->miss[k];
    if (key_in(row->miss[k].key, g_link_keys, NVIDIA_GPU_NELEM(g_link_keys)))
      out[n].code =
          (unsigned char)nvidia_gpu_estimate_counter_code((enum stats_sentinel)row->miss[k].code);
    n++;
  }
  group[EST_FLOPS] = codes->flops;
  group[EST_MEM] = codes->mem;
  group[EST_LINK] = codes->link;
  for (size_t d = 0; d < NVIDIA_GPU_NELEM(g_derived); d++) {
    enum stats_sentinel c = group[g_derived[d].group];

    if (c == STATS_SENTINEL_OK)
      continue;
    out[n].key = g_derived[d].key;
    out[n].code = (unsigned char)(g_derived[d].counter ? nvidia_gpu_estimate_counter_code(c) : c);
    n++;
  }
  return n;
}

unsigned long long nvidia_gpu_rate_integrate(double rate_per_s, long long delta_us,
                                             unsigned long long *acc)
{
  double add;
  unsigned long long inc = ULLONG_MAX;

  if (acc == NULL || delta_us <= 0)
    return 0ULL;
  add = rate_per_s * ((double)delta_us / 1000000.0);
  if (add <= 0.0)
    return 0ULL;
  if (add < (double)ULLONG_MAX)
    inc = (unsigned long long)(add + 0.5);
  *acc += inc;
  return inc;
}

#define NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S 1000000000000.0

void nvidia_gpu_estimate_rates(const dcgm_data_t *row, double *flops_rate_out,
                               double *mem_bw_rate_out)
{
  double per_clock = 0.0;
  unsigned int sms;
  int i;

  if (flops_rate_out != NULL)
    *flops_rate_out = 0.0;
  if (mem_bw_rate_out != NULL)
    *mem_bw_rate_out = 0.0;
  if (row == NULL)
    return;

  i = peak_index(row->cc_major, row->cc_minor);
  sms = nvidia_gpu_sm_count(row);
  if (flops_rate_out != NULL && i >= 0 && sms > 0 && row->sm_clock > 0) {
    for (int p = 0; p < NVIDIA_GPU_PIPE_NR; p++) {
      double ratio;

      memcpy(&ratio, (const char *)row + g_pipes[p].offset, sizeof(ratio));
      if (ratio > 0.0)
        per_clock += (ratio > 1.0 ? 1.0 : ratio) * (double)g_peaks[i].flops[p];
    }
    *flops_rate_out = per_clock * (double)sms * (double)row->sm_clock * 1.0e6;
  }
  if (mem_bw_rate_out != NULL && row->mem_util > 0)
    *mem_bw_rate_out = ((double)row->mem_util / 100.0) * NVIDIA_GPU_APPROX_PEAK_MEM_BW_BYTES_PER_S;
}
