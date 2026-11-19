#ifndef NVIDIA_GPU_ESTIMATE_H_
#define NVIDIA_GPU_ESTIMATE_H_

#include <stdint.h>

#include "nvidia_gpu.h"
#include "stats_sentinel.h"

/* Pipes counted in gpu_flops_rate; each pairs a duty-cycle key with a per-SM peak. DFMA (the FP64
 * FMA pipe, same as fp64_active), IMMA (integer), integer_active and the tensor_active remainder
 * not attributed to HMMA / DMMA are not FLOP pipes. */
enum nvidia_gpu_pipe {
  NVIDIA_GPU_PIPE_FP64, /* fp64_active */
  NVIDIA_GPU_PIPE_FP32, /* fp32_active */
  NVIDIA_GPU_PIPE_FP16, /* fp16_active, non-tensor */
  NVIDIA_GPU_PIPE_HMMA, /* tensor_hmma_active at the dense FP16 tensor peak */
  NVIDIA_GPU_PIPE_DMMA, /* tensor_dmma_active at the FP64 tensor peak */
  NVIDIA_GPU_PIPE_NR
};

/* FLOPs per SM per clock of pipe on compute capability major.minor; 0 for an unknown CC or a pipe
 * whose rate is not grounded in an NVIDIA document. */
double nvidia_gpu_flops_per_sm_clock(int major, int minor, enum nvidia_gpu_pipe pipe);
/* SMs of row: its NVML multiprocessor count, else NVML CUDA cores / FP32 lanes per SM of its CC;
 * 0 when unknown (or row NULL). */
unsigned int nvidia_gpu_sm_count(const dcgm_data_t *row);
/* gpu_flops_rate = sum over pipes of duty cycle x per-SM peak, x SMs x sm_clock (MHz); 0 when the
 * CC, SM count or clock is unknown (the derived keys are then NOT_PRESENT, see row_codes).
 * mem_bw_rate = gpu_mem_util % of a fixed 1 TB/s. NULL row zeroes both outputs. */
void nvidia_gpu_estimate_rates(const dcgm_data_t *row, double *flops_rate_out,
                               double *mem_bw_rate_out);

/* DCGM PROF 1009-1012 and GPM PCIe/NvLink are byte RATES (B/s), not counters: add
 * rate x delta_us into *acc (saturating) and return the bytes added; 0 when delta_us <= 0. */
unsigned long long nvidia_gpu_link_rate_integrate(uint64_t rate_bps, long long delta_us,
                                                  unsigned long long *acc);

/* Sentinel of each derived-key input group of one row: STATS_SENTINEL_OK when every input was
 * read, else the code of the first row->miss entry that names one of the group's inputs. */
struct nvidia_gpu_estimate_codes {
  enum stats_sentinel flops; /* shape, gpu_sm_clock, FLOP pipes -> gpu_flops_rate, gpu_flops */
  enum stats_sentinel mem;   /* gpu_mem_util -> gpu_mem_bw_bytes_rate, gpu_mem_*_bytes */
  enum stats_sentinel link;  /* PCIe/NvLink rates -> gpu_io_link_total_bytes */
};
/* Derived keys published as missing when their input group is not OK. */
#define NVIDIA_GPU_ESTIMATE_DERIVED_KEYS 7
#define NVIDIA_GPU_ESTIMATE_MISS_MAX (NVIDIA_GPU_ROW_MISS_MAX + NVIDIA_GPU_ESTIMATE_DERIVED_KEYS)

/* Fill out for row; a NULL row has no inputs (every group NOT_PRESENT). flops is NOT_PRESENT for
 * an unknown CC or SM count; else gpu_sm_clock's code; else the first code other than NOT_PRESENT
 * of a pipe with a peak on this CC (a NOT_PRESENT pipe drops out of the sum), and NOT_PRESENT when
 * every such pipe is NOT_PRESENT. */
void nvidia_gpu_estimate_row_codes(const dcgm_data_t *row, struct nvidia_gpu_estimate_codes *out);
/* Code a cumulative (E) key integrated from an input carries when that input was not read: the
 * tick's activity is not integrated, so a bridgeable code becomes NOT_SCHEDULED
 * (monitor-value-sentinel-contract law 4); every other code is kept. */
enum stats_sentinel nvidia_gpu_estimate_counter_code(enum stats_sentinel input);
/* Every key of row to publish as missing into out: row->miss (the integrated link E keys through
 * nvidia_gpu_estimate_counter_code) plus the derived keys of each non-OK group in codes.
 * Returns the count. */
int nvidia_gpu_estimate_miss_list(const dcgm_data_t *row,
                                  const struct nvidia_gpu_estimate_codes *codes,
                                  struct nvidia_gpu_row_miss out[NVIDIA_GPU_ESTIMATE_MISS_MAX]);

#endif
