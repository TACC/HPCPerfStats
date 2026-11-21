/* nvidia_gpu_estimate: per-compute-capability FLOP peaks, gpu_flops_rate, sentinel codes of the
 * derived keys, nvidia_gpu_rate_integrate. */
#include <assert.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "nvidia_gpu_estimate.h"

/* Record a not-read key on row the way the collectors' nvidia_gpu_row_mark does. */
static void mark(dcgm_data_t *row, const char *key, enum stats_sentinel code)
{
  row->miss[row->nmiss].key = key;
  row->miss[row->nmiss].code = (unsigned char)code;
  row->nmiss++;
}

/* A row with a known H100 SXM5 shape: CC 9.0, 132 SMs, SM clock 1980 MHz. */
static void h100_row(dcgm_data_t *row)
{
  memset(row, 0, sizeof(*row));
  row->cc_major = 9;
  row->cc_minor = 0;
  row->sm_count = 132;
  row->sm_clock = 1980;
}

static void test_null_input_zeroes_outputs(void)
{
  double flops = 99.0;
  double mem_bw = 99.0;

  nvidia_gpu_estimate_rates(NULL, &flops, &mem_bw);
  assert(flops == 0.0);
  assert(mem_bw == 0.0);
}

/* peak_table: every tabulated FLOPs-per-SM-per-clock value, and its product with the SM count
 * and the reference clock against the per-GPU peak NVIDIA publishes for that part (within 2 %:
 * datasheets round to two or three significant figures, e.g. GH200 FP64 34 for 33.45).
 * Sources: A100 SXM4 - GA100 whitepaper / Hopper whitepaper Table 3 (108 SMs, 1410 MHz boost);
 * H100 SXM5 and GH200 - H100 / GH200 datasheets (132 SMs; scalar and FP64 tensor peaks at the
 * 1980 MHz max boost clock, FP16 tensor 989.4 TFLOPS dense at 1830 MHz). */
static void test_peak_table_per_cc_matches_published_peaks(void)
{
  static const struct {
    const char *part;
    int major, minor;
    enum nvidia_gpu_pipe pipe;
    double per_sm_clk;
    unsigned int sms;
    double ref_mhz;
    double published_tflops; /* 0: no NVIDIA per-GPU peak with a published clock */
  } t[] = {
      {"A100", 8, 0, NVIDIA_GPU_PIPE_FP64, 64.0, 108, 1410.0, 9.7},
      {"A100", 8, 0, NVIDIA_GPU_PIPE_FP32, 128.0, 108, 1410.0, 19.5},
      {"A100", 8, 0, NVIDIA_GPU_PIPE_FP16, 512.0, 108, 1410.0, 78.0},
      {"A100", 8, 0, NVIDIA_GPU_PIPE_HMMA, 2048.0, 108, 1410.0, 312.0},
      {"A100", 8, 0, NVIDIA_GPU_PIPE_DMMA, 128.0, 108, 1410.0, 19.5},
      {"H100", 9, 0, NVIDIA_GPU_PIPE_FP64, 128.0, 132, 1980.0, 33.5},
      {"H100", 9, 0, NVIDIA_GPU_PIPE_FP32, 256.0, 132, 1980.0, 66.9},
      {"H100", 9, 0, NVIDIA_GPU_PIPE_FP16, 512.0, 132, 1980.0, 133.8},
      {"H100", 9, 0, NVIDIA_GPU_PIPE_HMMA, 4096.0, 132, 1830.0, 989.4},
      {"H100", 9, 0, NVIDIA_GPU_PIPE_DMMA, 256.0, 132, 1980.0, 66.9},
      {"GH200", 9, 0, NVIDIA_GPU_PIPE_FP64, 128.0, 132, 1980.0, 34.0},
      {"GH200", 9, 0, NVIDIA_GPU_PIPE_HMMA, 4096.0, 132, 1830.0, 989.4},
      {"GH200", 9, 0, NVIDIA_GPU_PIPE_DMMA, 256.0, 132, 1980.0, 67.0},
      /* CC 10.0 / 12.0: scalar pipes from the CUDA C++ Programming Guide throughput table;
       * no NVIDIA document gives the clock behind the B200 / RTX PRO 6000 datasheet peaks. */
      {"B200", 10, 0, NVIDIA_GPU_PIPE_FP64, 128.0, 148, 0.0, 0.0},
      {"B200", 10, 0, NVIDIA_GPU_PIPE_FP32, 256.0, 148, 0.0, 0.0},
      {"B200", 10, 0, NVIDIA_GPU_PIPE_FP16, 512.0, 148, 0.0, 0.0},
      {"RTX PRO 6000", 12, 0, NVIDIA_GPU_PIPE_FP64, 4.0, 188, 0.0, 0.0},
      {"RTX PRO 6000", 12, 0, NVIDIA_GPU_PIPE_FP32, 256.0, 188, 0.0, 0.0},
      {"RTX PRO 6000", 12, 0, NVIDIA_GPU_PIPE_FP16, 512.0, 188, 0.0, 0.0},
  };
  size_t i;

  for (i = 0; i < sizeof(t) / sizeof(t[0]); i++) {
    double v = nvidia_gpu_flops_per_sm_clock(t[i].major, t[i].minor, t[i].pipe);
    double tflops = v * (double)t[i].sms * t[i].ref_mhz * 1.0e6 / 1.0e12;

    if (v != t[i].per_sm_clk)
      fprintf(stderr, "%s pipe %d: %g != %g\n", t[i].part, (int)t[i].pipe, v, t[i].per_sm_clk);
    assert(v == t[i].per_sm_clk);
    if (t[i].published_tflops > 0.0)
      assert(fabs(tflops - t[i].published_tflops) <= 0.02 * t[i].published_tflops);
  }
}

/* Pipes without a grounded per-SM rate, and unknown compute capabilities, have no peak. */
static void test_peak_table_ungrounded_and_unknown_cc_are_zero(void)
{
  static const int unknown[][2] = {{0, 0}, {7, 0}, {8, 6}, {8, 9}, {9, 1}, {10, 3}, {12, 1}};
  size_t i;
  int p;

  assert(nvidia_gpu_flops_per_sm_clock(10, 0, NVIDIA_GPU_PIPE_HMMA) == 0.0);
  assert(nvidia_gpu_flops_per_sm_clock(10, 0, NVIDIA_GPU_PIPE_DMMA) == 0.0);
  assert(nvidia_gpu_flops_per_sm_clock(12, 0, NVIDIA_GPU_PIPE_HMMA) == 0.0);
  assert(nvidia_gpu_flops_per_sm_clock(12, 0, NVIDIA_GPU_PIPE_DMMA) == 0.0);
  for (i = 0; i < sizeof(unknown) / sizeof(unknown[0]); i++)
    for (p = 0; p < NVIDIA_GPU_PIPE_NR; p++)
      assert(nvidia_gpu_flops_per_sm_clock(unknown[i][0], unknown[i][1], (enum nvidia_gpu_pipe)p) ==
             0.0);
  assert(nvidia_gpu_flops_per_sm_clock(9, 0, NVIDIA_GPU_PIPE_NR) == 0.0);
}

/* SM count: the NVML multiprocessor count when known, else NVML CUDA cores / FP32 lanes per SM. */
static void test_sm_count_from_attributes_or_cores(void)
{
  dcgm_data_t row;

  h100_row(&row);
  assert(nvidia_gpu_sm_count(&row) == 132);
  row.sm_count = 0;
  row.gpu_cores = 16896;
  assert(nvidia_gpu_sm_count(&row) == 132);
  row.cc_major = 8;
  row.gpu_cores = 6912;
  assert(nvidia_gpu_sm_count(&row) == 108);
  row.cc_major = 12;
  row.gpu_cores = 24064;
  assert(nvidia_gpu_sm_count(&row) == 188);
  row.cc_major = 7; /* unknown CC: lanes per SM unknown */
  assert(nvidia_gpu_sm_count(&row) == 0);
  row.cc_major = 9;
  row.gpu_cores = 0;
  assert(nvidia_gpu_sm_count(&row) == 0);
  assert(nvidia_gpu_sm_count(NULL) == 0);
}

/* rate = (fp64 x P64 + fp32 x P32 + fp16 x P16 + hmma x PT16 + dmma x PT64) x SMs x clock.
 * DFMA (same pipe as fp64_active on Hopper / Blackwell), IMMA (integer), integer_active and the
 * unattributed tensor remainder (tensor_active) are not in it. */
static void test_flops_rate_per_pipe_formula(void)
{
  dcgm_data_t row;
  double flops;
  double mem_bw;
  double want;

  h100_row(&row);
  row.fp64_active = 0.5;
  row.fp32_active = 0.25;
  row.fp16_active = 0.1;
  row.tensor_hmma_active = 0.2;
  row.tensor_dmma_active = 0.05;
  row.tensor_dfma_active = 0.5;
  row.tensor_imma_active = 0.3;
  row.tensor_active = 0.9;
  row.integer_active = 0.5;
  nvidia_gpu_estimate_rates(&row, &flops, &mem_bw);
  want = (0.5 * 128.0 + 0.25 * 256.0 + 0.1 * 512.0 + 0.2 * 4096.0 + 0.05 * 256.0) * 132.0 * 1.98e9;
  assert(fabs(flops - want) <= 1e-9 * want);
  assert(mem_bw == 0.0);

  /* The old single 60 TF/s estimate would give 0.5 x 60e12 for fp64 0.25 + fp32 0.25. */
  h100_row(&row);
  row.fp64_active = 0.25;
  row.fp32_active = 0.25;
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  want = (0.25 * 128.0 + 0.25 * 256.0) * 132.0 * 1.98e9;
  assert(fabs(flops - want) <= 1e-9 * want);
  assert(fabs(flops - 0.5 * 60000000000000.0) > 1.0e12);

  /* A100 by core count, full HMMA: 312 TFLOPS dense at 1410 MHz. */
  memset(&row, 0, sizeof(row));
  row.cc_major = 8;
  row.gpu_cores = 6912;
  row.sm_clock = 1410;
  row.tensor_hmma_active = 1.0;
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  assert(fabs(flops - 311.87e12) < 0.01e12);
}

/* Blackwell tensor pipes have no grounded peak: their activity adds nothing, scalar pipes do. */
static void test_flops_rate_blackwell_scalar_only(void)
{
  dcgm_data_t row;
  double flops;

  memset(&row, 0, sizeof(row));
  row.cc_major = 10;
  row.sm_count = 148;
  row.sm_clock = 1965;
  row.fp32_active = 1.0;
  row.tensor_hmma_active = 0.76;
  row.tensor_dmma_active = 0.5;
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  assert(fabs(flops - 256.0 * 148.0 * 1.965e9) < 1.0e6);
}

/* Unknown CC, missing SM count or no clock: never the old constant, and the derived keys are
 * NOT_PRESENT (a clock read failure carries its own code). */
static void test_flops_unknown_cc_or_shape_not_present(void)
{
  struct nvidia_gpu_estimate_codes codes;
  dcgm_data_t row;
  double flops;

  h100_row(&row);
  row.cc_major = 7;
  row.fp64_active = 0.9;
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(flops == 0.0 && codes.flops == STATS_SENTINEL_NOT_PRESENT);

  h100_row(&row);
  row.cc_major = 0; /* NVML CC query failed */
  row.fp64_active = 0.9;
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(flops == 0.0 && codes.flops == STATS_SENTINEL_NOT_PRESENT);

  h100_row(&row);
  row.sm_count = 0;
  row.gpu_cores = 0;
  row.fp64_active = 0.9;
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(flops == 0.0 && codes.flops == STATS_SENTINEL_NOT_PRESENT);

  h100_row(&row);
  row.sm_clock = 0;
  row.fp64_active = 0.9;
  mark(&row, "gpu_sm_clock", STATS_SENTINEL_NOT_PRESENT);
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(flops == 0.0 && codes.flops == STATS_SENTINEL_NOT_PRESENT);

  h100_row(&row);
  mark(&row, "gpu_sm_clock", STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_READ_FAILED);
}

/* dmma on the DCGM path: tensor_dmma_active is NOT_PRESENT (no DCGM field) and drops out of the
 * sum; the estimate stays OK. A pipe that failed this tick spoils the sum (its code); a pipe with
 * no peak for the CC is ignored whatever its code; every counted pipe NOT_PRESENT -> NOT_PRESENT. */
static void test_flops_codes_per_pipe(void)
{
  struct nvidia_gpu_estimate_codes codes;
  dcgm_data_t row;
  double flops;

  h100_row(&row);
  row.fp64_active = 0.5;
  mark(&row, "tensor_dmma_active", STATS_SENTINEL_NOT_PRESENT);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  nvidia_gpu_estimate_rates(&row, &flops, NULL);
  assert(codes.flops == STATS_SENTINEL_OK);
  assert(fabs(flops - 0.5 * 128.0 * 132.0 * 1.98e9) < 1.0e6);

  h100_row(&row);
  mark(&row, "tensor_dmma_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "tensor_hmma_active", STATS_SENTINEL_NOT_UPDATED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_NOT_UPDATED);

  /* tensor_active / tensor_dfma_active / integer_active are not inputs any more. */
  h100_row(&row);
  mark(&row, "tensor_active", STATS_SENTINEL_READ_FAILED);
  mark(&row, "tensor_dfma_active", STATS_SENTINEL_READ_FAILED);
  mark(&row, "integer_active", STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_OK);

  memset(&row, 0, sizeof(row));
  row.cc_major = 12;
  row.sm_count = 188;
  row.sm_clock = 2400;
  mark(&row, "tensor_hmma_active", STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_OK);

  h100_row(&row);
  mark(&row, "fp64_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "fp32_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "fp16_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "tensor_hmma_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "tensor_dmma_active", STATS_SENTINEL_NOT_PRESENT);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_NOT_PRESENT);
}

static void test_mem_util_scales_bandwidth(void)
{
  dcgm_data_t row;
  double flops;
  double mem_bw;

  h100_row(&row);
  row.mem_util = 50;
  nvidia_gpu_estimate_rates(&row, &flops, &mem_bw);
  assert(flops == 0.0);
  assert(fabs(mem_bw - 0.5 * 1000000000000.0) < 1.0);
  row.mem_util = -5;
  nvidia_gpu_estimate_rates(&row, &flops, &mem_bw);
  assert(mem_bw == 0.0);
}

static void test_link_rate_integrate_accumulates(void)
{
  unsigned long long acc = 0ULL;

  /* c122-018 idle PCIe TX 175 B/s over a 60 s collect = 10500 B. */
  assert(nvidia_gpu_rate_integrate(175.0, 60000000LL, &acc) == 10500ULL);
  /* The rate dropped (175 -> 157) but the counter still grows (was "decreased"). */
  assert(nvidia_gpu_rate_integrate(157.0, 60000000LL, &acc) == 9420ULL);
  assert(acc == 19920ULL);
  /* 2.653e11 B/s NvLink for 0.5 s. */
  acc = 0ULL;
  assert(nvidia_gpu_rate_integrate(265333528373.0, 500000LL, &acc) == 132666764187ULL);
}

static void test_link_rate_integrate_first_collect_and_null(void)
{
  unsigned long long acc = 42ULL;

  assert(nvidia_gpu_rate_integrate(1000.0, 0LL, &acc) == 0ULL);
  assert(nvidia_gpu_rate_integrate(1000.0, -5LL, &acc) == 0ULL);
  assert(acc == 42ULL);
  assert(nvidia_gpu_rate_integrate(1000.0, 1000000LL, NULL) == 0ULL);
}

static void test_rate_integrate_wraps_like_a_counter(void)
{
  unsigned long long acc = ULLONG_MAX - 10ULL;

  /* W=64 lifetime counters wrap modulo 2^64 (the consumer undoes the wrap); saturating at
   * 2^64 - 1 froze gpu_flops after about 1.8e19 FLOPs and its delta read 0 from then on. */
  assert(nvidia_gpu_rate_integrate(100.0, 1000000LL, &acc) == 100ULL);
  assert(acc == 89ULL);
  assert(nvidia_gpu_rate_integrate(100.0, 1000000LL, &acc) == 100ULL);
  assert(acc == 189ULL);
  /* An increment above 2^64 is clamped, never undefined. */
  acc = 0ULL;
  assert(nvidia_gpu_rate_integrate(1e30, 3600000000LL, &acc) == ULLONG_MAX);
  assert(acc == ULLONG_MAX);
}

static void test_rate_integrate_takes_fractional_rates(void)
{
  unsigned long long acc = 0ULL;

  /* gpu_flops: 4.95e14 FLOP/s over 30 s; mem bytes from a double rate too. */
  assert(nvidia_gpu_rate_integrate(4.95e14, 30000000LL, &acc) == 14850000000000000ULL);
  assert(nvidia_gpu_rate_integrate(0.4, 1000000LL, &acc) == 0ULL);
  assert(nvidia_gpu_rate_integrate(-5.0, 1000000LL, &acc) == 0ULL);
  assert(acc == 14850000000000000ULL);
}

static unsigned int list_code(const struct nvidia_gpu_row_miss *miss, int n, const char *key)
{
  for (int i = 0; i < n; i++)
    if (strcmp(miss[i].key, key) == 0)
      return miss[i].code;
  return 0;
}

/* flops_input_missing: gpu_flops_rate / gpu_flops were computed from absent inputs (a 0 PROF
 * ratio) and published as real values. Each derived key now carries its input's sentinel; the
 * cumulative E keys turn a bridgeable input code into NOT_SCHEDULED (the tick's activity is not
 * integrated). Keys whose inputs were read stay unmarked. */
static void test_flops_input_missing_derived_keys_carry_sentinel(void)
{
  struct nvidia_gpu_row_miss miss[NVIDIA_GPU_ESTIMATE_MISS_MAX];
  struct nvidia_gpu_estimate_codes codes;
  dcgm_data_t row;
  int n;

  /* Every input read: no codes, the list is just the row's own marks. */
  h100_row(&row);
  mark(&row, "temperature", STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_OK && codes.mem == STATS_SENTINEL_OK &&
         codes.link == STATS_SENTINEL_OK);
  n = nvidia_gpu_estimate_miss_list(&row, &codes, miss);
  assert(n == 1 && list_code(miss, n, "temperature") == STATS_SENTINEL_READ_FAILED);

  /* fp32_active failed (bridgeable): rate READ_FAILED, counter NOT_SCHEDULED; mem untouched. */
  h100_row(&row);
  mark(&row, "fp32_active", STATS_SENTINEL_READ_FAILED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_READ_FAILED && codes.mem == STATS_SENTINEL_OK);
  n = nvidia_gpu_estimate_miss_list(&row, &codes, miss);
  assert(n == 3);
  assert(list_code(miss, n, "gpu_flops_rate") == STATS_SENTINEL_READ_FAILED);
  assert(list_code(miss, n, "gpu_flops") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(list_code(miss, n, "gpu_mem_bw_bytes_rate") == 0);

  /* basic-nonprof DCGM profile: PROF inputs NOT_PRESENT (non-bridgeable, kept as is); mem_util
   * missing; a link rate missing marks the integrated link E key and the link total. */
  h100_row(&row);
  mark(&row, "fp64_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "fp32_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "fp16_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "tensor_hmma_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "tensor_dmma_active", STATS_SENTINEL_NOT_PRESENT);
  mark(&row, "gpu_mem_util", STATS_SENTINEL_INVALID_VALUE);
  mark(&row, "gpu_nvlink_rx_bytes", STATS_SENTINEL_NOT_UPDATED);
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_NOT_PRESENT);
  assert(codes.mem == STATS_SENTINEL_INVALID_VALUE);
  assert(codes.link == STATS_SENTINEL_NOT_UPDATED);
  n = nvidia_gpu_estimate_miss_list(&row, &codes, miss);
  assert(n == 7 + NVIDIA_GPU_ESTIMATE_DERIVED_KEYS);
  assert(list_code(miss, n, "gpu_flops_rate") == STATS_SENTINEL_NOT_PRESENT);
  assert(list_code(miss, n, "gpu_flops") == STATS_SENTINEL_NOT_PRESENT);
  assert(list_code(miss, n, "gpu_mem_bw_bytes_rate") == STATS_SENTINEL_INVALID_VALUE);
  assert(list_code(miss, n, "gpu_mem_read_bytes") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(list_code(miss, n, "gpu_mem_write_bytes") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(list_code(miss, n, "gpu_mem_total_bytes") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(list_code(miss, n, "gpu_io_link_total_bytes") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(list_code(miss, n, "gpu_nvlink_rx_bytes") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(list_code(miss, n, "tensor_dmma_active") == STATS_SENTINEL_NOT_PRESENT);

  /* The counter mapping keeps every non-bridgeable code; NULL row means no inputs read. */
  assert(nvidia_gpu_estimate_counter_code(STATS_SENTINEL_OK) == STATS_SENTINEL_OK);
  assert(nvidia_gpu_estimate_counter_code(STATS_SENTINEL_BACKOFF) == STATS_SENTINEL_NOT_SCHEDULED);
  assert(nvidia_gpu_estimate_counter_code(STATS_SENTINEL_PERMISSION_DENIED) ==
         STATS_SENTINEL_PERMISSION_DENIED);
  nvidia_gpu_estimate_row_codes(NULL, &codes);
  assert(codes.flops == STATS_SENTINEL_NOT_PRESENT && codes.link == STATS_SENTINEL_NOT_PRESENT);
}

/* gpm_back_to_back: a collect inside the GPM min gap republishes the last window's rates with no
 * sentinel, so the integrated E keys stay OK and grow by rate x the real (tiny) delta: every
 * elapsed microsecond is integrated once, no window is counted twice. */
static void test_gpm_back_to_back_integrates_real_delta(void)
{
  struct nvidia_gpu_row_miss miss[NVIDIA_GPU_ESTIMATE_MISS_MAX];
  struct nvidia_gpu_estimate_codes codes;
  unsigned long long acc = 0ULL;
  dcgm_data_t row;

  h100_row(&row);
  row.prof_nvlink_tx_bytes = 265289728000ULL;
  nvidia_gpu_estimate_row_codes(&row, &codes);
  assert(codes.flops == STATS_SENTINEL_OK && codes.link == STATS_SENTINEL_OK);
  assert(nvidia_gpu_estimate_miss_list(&row, &codes, miss) == 0);
  assert(nvidia_gpu_rate_integrate(row.prof_nvlink_tx_bytes, 30000000LL, &acc) == 7958691840000ULL);
  /* Back-to-back tick 40 ms later: + rate x 0.04 s, not another 30 s window. */
  assert(nvidia_gpu_rate_integrate(row.prof_nvlink_tx_bytes, 40000LL, &acc) == 10611589120ULL);
  assert(acc == 7958691840000ULL + 10611589120ULL);
}

int main(void)
{
  test_peak_table_per_cc_matches_published_peaks();
  test_peak_table_ungrounded_and_unknown_cc_are_zero();
  test_sm_count_from_attributes_or_cores();
  test_flops_rate_per_pipe_formula();
  test_flops_rate_blackwell_scalar_only();
  test_flops_unknown_cc_or_shape_not_present();
  test_flops_codes_per_pipe();
  test_gpm_back_to_back_integrates_real_delta();
  test_flops_input_missing_derived_keys_carry_sentinel();
  test_null_input_zeroes_outputs();
  test_mem_util_scales_bandwidth();
  test_link_rate_integrate_accumulates();
  test_link_rate_integrate_first_collect_and_null();
  test_rate_integrate_wraps_like_a_counter();
  test_rate_integrate_takes_fractional_rates();
  printf("test_nvidia_gpu_estimate passed\n");
  return 0;
}
