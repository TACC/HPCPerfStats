// Bounded cuBLAS GEMM load with an exact FLOP count, for calibrating the per-SM-per-clock tensor
// peaks (g_peaks HMMA / DMMA columns) of src/nvidia_gpu_estimate.c. Measurement kit only; built
// and run by bin/gpu_tensor_calibrate.sh, never part of the daemon or its packages.
//
//   --kind hmma: cublasGemmEx, FP16 A/B, FP32 C, FP32 compute (tensor HMMA pipe)
//   --kind dmma: cublasDgemm (FP64 tensor DMMA pipe where the GPU has one, CC 10.0)
//
// Square n x n x n GEMMs: every call is exactly 2*n^3 FLOPs. Warmup calls are excluded; the timed
// window is measured with cudaEvents and bounded by --seconds (checked after each batch of calls
// sized to ~0.1 s). The result line carries the wall-clock edges of that window so the caller can
// pick the monitor samples that lie inside it.
#include <cublas_v2.h>
#include <cuda_fp16.h>
#include <cuda_runtime.h>

#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>

#define CUDA_OK(call)                                                                              \
  do {                                                                                             \
    cudaError_t e_ = (call);                                                                       \
    if (e_ != cudaSuccess) {                                                                       \
      printf("gpu_tensor_calibrate error=%s %s\n", #call, cudaGetErrorString(e_));                \
      return 1;                                                                                    \
    }                                                                                              \
  } while (0)
#define CUBLAS_OK(call)                                                                            \
  do {                                                                                             \
    cublasStatus_t s_ = (call);                                                                    \
    if (s_ != CUBLAS_STATUS_SUCCESS) {                                                             \
      printf("gpu_tensor_calibrate error=%s status %d\n", #call, (int)s_);                        \
      return 1;                                                                                    \
    }                                                                                              \
  } while (0)

static const int kWarmupCalls = 3;
static const double kBatchSeconds = 0.1;

// Deterministic values in [-1, 1): realistic switching activity (all-zero inputs draw less power
// and change the clock the tensor pipes run at).
template <typename T> __global__ void fill(T *p, size_t count, unsigned int seed)
{
  for (size_t i = blockIdx.x * (size_t)blockDim.x + threadIdx.x; i < count;
       i += (size_t)gridDim.x * blockDim.x) {
    unsigned int h = (unsigned int)i * 2654435761u ^ seed;
    h ^= h >> 15;
    h *= 2246822519u;
    h ^= h >> 13;
    p[i] = (T)((float)(h & 0xffffu) / 32768.0f - 1.0f);
  }
}

static double wall_now(void)
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

struct gemm {
  cublasHandle_t h;
  int dmma;
  int n;
  void *a, *b, *c;
};

static cublasStatus_t gemm_call(const struct gemm *g)
{
  const float alpha_f = 1.0f, beta_f = 0.0f;
  const double alpha_d = 1.0, beta_d = 0.0;

  if (g->dmma)
    return cublasDgemm(g->h, CUBLAS_OP_N, CUBLAS_OP_N, g->n, g->n, g->n, &alpha_d,
                       (const double *)g->a, g->n, (const double *)g->b, g->n, &beta_d,
                       (double *)g->c, g->n);
  return cublasGemmEx(g->h, CUBLAS_OP_N, CUBLAS_OP_N, g->n, g->n, g->n, &alpha_f, g->a, CUDA_R_16F,
                      g->n, g->b, CUDA_R_16F, g->n, &beta_f, g->c, CUDA_R_32F, g->n,
                      CUBLAS_COMPUTE_32F, CUBLAS_GEMM_DEFAULT);
}

static int usage(void)
{
  fprintf(stderr, "usage: gpu_tensor_calibrate --kind hmma|dmma --n N --seconds S [--device D]\n");
  return 2;
}

int main(int argc, char **argv)
{
  const char *kind = NULL;
  long n = 0;
  double seconds = 0.0;
  int device = 0;
  struct gemm g;
  cudaDeviceProp prop;
  int major, minor, sms, rt_ver, blas_ver;
  char pci[32];
  size_t count, in_bytes, out_bytes;
  cudaEvent_t start, stop;
  float ms = 0.0f;
  unsigned long long per_call, calls = 0;
  long batch;
  double t0, t1, secs;

  for (int i = 1; i + 1 < argc; i += 2) {
    if (strcmp(argv[i], "--kind") == 0)
      kind = argv[i + 1];
    else if (strcmp(argv[i], "--n") == 0)
      n = strtol(argv[i + 1], NULL, 10);
    else if (strcmp(argv[i], "--seconds") == 0)
      seconds = strtod(argv[i + 1], NULL);
    else if (strcmp(argv[i], "--device") == 0)
      device = (int)strtol(argv[i + 1], NULL, 10);
    else
      return usage();
  }
  if (argc % 2 == 0 || kind == NULL || (strcmp(kind, "hmma") != 0 && strcmp(kind, "dmma") != 0) ||
      n < 256 || n > 32768 || seconds <= 0.0 || device < 0)
    return usage();

  memset(&g, 0, sizeof(g));
  g.dmma = strcmp(kind, "dmma") == 0;
  g.n = (int)n;
  CUDA_OK(cudaSetDevice(device));
  CUDA_OK(cudaGetDeviceProperties(&prop, device));
  CUDA_OK(cudaDeviceGetAttribute(&major, cudaDevAttrComputeCapabilityMajor, device));
  CUDA_OK(cudaDeviceGetAttribute(&minor, cudaDevAttrComputeCapabilityMinor, device));
  CUDA_OK(cudaDeviceGetAttribute(&sms, cudaDevAttrMultiProcessorCount, device));
  CUDA_OK(cudaDeviceGetPCIBusId(pci, (int)sizeof(pci), device));
  CUDA_OK(cudaRuntimeGetVersion(&rt_ver));
  for (char *s = prop.name; *s; s++)
    if (*s == ' ')
      *s = '_';

  count = (size_t)n * (size_t)n;
  in_bytes = count * (g.dmma ? sizeof(double) : sizeof(__half));
  out_bytes = count * (g.dmma ? sizeof(double) : sizeof(float));
  CUDA_OK(cudaMalloc(&g.a, in_bytes));
  CUDA_OK(cudaMalloc(&g.b, in_bytes));
  CUDA_OK(cudaMalloc(&g.c, out_bytes));
  if (g.dmma) {
    fill<double><<<1024, 256>>>((double *)g.a, count, 1u);
    fill<double><<<1024, 256>>>((double *)g.b, count, 2u);
  } else {
    fill<__half><<<1024, 256>>>((__half *)g.a, count, 1u);
    fill<__half><<<1024, 256>>>((__half *)g.b, count, 2u);
  }
  CUDA_OK(cudaGetLastError());
  CUDA_OK(cudaMemset(g.c, 0, out_bytes));

  CUBLAS_OK(cublasCreate(&g.h));
  CUBLAS_OK(cublasGetVersion(g.h, &blas_ver));
  // Default math: no FP32/FP64 emulation modes, tensor cores allowed for FP16 inputs.
  CUBLAS_OK(cublasSetMathMode(g.h, CUBLAS_DEFAULT_MATH));
  CUDA_OK(cudaEventCreate(&start));
  CUDA_OK(cudaEventCreate(&stop));

  // Warmup (heuristic selection, workspace allocation, clock ramp), then time the same count
  // once more to size a batch.
  for (int i = 0; i < kWarmupCalls; i++)
    CUBLAS_OK(gemm_call(&g));
  CUDA_OK(cudaDeviceSynchronize());
  CUDA_OK(cudaEventRecord(start));
  for (int i = 0; i < kWarmupCalls; i++)
    CUBLAS_OK(gemm_call(&g));
  CUDA_OK(cudaEventRecord(stop));
  CUDA_OK(cudaEventSynchronize(stop));
  CUDA_OK(cudaEventElapsedTime(&ms, start, stop));
  batch = (long)(kBatchSeconds * 1000.0 * kWarmupCalls / (ms > 0.0f ? ms : 1e-3f)) + 1;
  printf("gpu_tensor_calibrate warmup_calls=%d warmup_ms=%.3f batch=%ld cuda_runtime=%d cublas=%d\n",
         2 * kWarmupCalls, ms, batch, rt_ver, blas_ver);

  per_call = 2ULL * (unsigned long long)n * (unsigned long long)n * (unsigned long long)n;
  CUDA_OK(cudaDeviceSynchronize());
  t0 = wall_now();
  CUDA_OK(cudaEventRecord(start));
  for (;;) {
    for (long i = 0; i < batch; i++)
      CUBLAS_OK(gemm_call(&g));
    calls += (unsigned long long)batch;
    CUDA_OK(cudaEventRecord(stop));
    CUDA_OK(cudaEventSynchronize(stop));
    CUDA_OK(cudaEventElapsedTime(&ms, start, stop));
    if (ms >= seconds * 1000.0 || calls + (unsigned long long)batch > ULLONG_MAX / per_call)
      break;
  }
  t1 = wall_now();
  secs = (double)ms / 1000.0;
  printf("gpu_tensor_calibrate kind=%s n=%ld device=%d pci=%s name=%s cc=%d.%d sms=%d calls=%llu "
         "flops=%llu elapsed_s=%.6f flops_per_s=%.6e start_epoch=%.6f end_epoch=%.6f\n",
         kind, n, device, pci, prop.name, major, minor, sms, calls, calls * per_call, secs,
         (double)(calls * per_call) / secs, t0, t1);

  cudaEventDestroy(start);
  cudaEventDestroy(stop);
  cublasDestroy(g.h);
  cudaFree(g.a);
  cudaFree(g.b);
  cudaFree(g.c);
  return 0;
}
