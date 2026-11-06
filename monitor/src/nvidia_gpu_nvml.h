#ifndef NVIDIA_GPU_NVML_H_
#define NVIDIA_GPU_NVML_H_

/*
 * nvidia_gpu source on GPM-capable GPUs (Hopper and newer): NVML getters plus one GPM sample
 * pair per GPU per collect, loaded with dlopen (libnvidia-ml.so.1, override
 * HPCPERFSTATS_NVML_LIB gated by dyn_lib_path_allowed). No background threads: embedded DCGM ran
 * three timer loops at ~400 wakeups/s for the same fields, NVML+GPM ran 0 (round 2, finding 19).
 * GPUs without GPM, or a node without libnvidia-ml, keep the DCGM path (D14).
 */

#include <stdint.h>

#include "nvidia_gpu.h"

#define NVIDIA_GPU_NVML_MAX_DEV 32
/* GPM needs > 100 ms between the two samples of a pair (nvml.h); shorter gaps skip PROF. */
#define NVIDIA_GPU_NVML_GPM_MIN_GAP_US 150000LL

typedef int nvidia_gpu_nvml_ret_t; /* nvmlReturn_t; 0 = NVML_SUCCESS */
typedef void *nvidia_gpu_nvml_dev_t;
typedef void *nvidia_gpu_nvml_sample_t;

/* The NVML entry points the monitor uses (dlsym'd; tests inject stubs). Layouts match nvml.h. */
struct nvidia_gpu_nvml_api {
  nvidia_gpu_nvml_ret_t (*init)(void);
  nvidia_gpu_nvml_ret_t (*shutdown)(void);
  const char *(*error_string)(nvidia_gpu_nvml_ret_t r);
  nvidia_gpu_nvml_ret_t (*device_count)(unsigned int *n);
  nvidia_gpu_nvml_ret_t (*device_by_index)(unsigned int i, nvidia_gpu_nvml_dev_t *d);
  nvidia_gpu_nvml_ret_t (*clock_info)(nvidia_gpu_nvml_dev_t d, int type, unsigned int *mhz);
  nvidia_gpu_nvml_ret_t (*power_usage)(nvidia_gpu_nvml_dev_t d, unsigned int *mw);
  nvidia_gpu_nvml_ret_t (*utilization)(nvidia_gpu_nvml_dev_t d, void *util); /* nvmlUtilization_t */
  nvidia_gpu_nvml_ret_t (*temperature)(nvidia_gpu_nvml_dev_t d, int sensor, unsigned int *c);
  nvidia_gpu_nvml_ret_t (*memory_info)(nvidia_gpu_nvml_dev_t d, void *mem); /* nvmlMemory_t */
  nvidia_gpu_nvml_ret_t (*clocks_event_reasons)(nvidia_gpu_nvml_dev_t d, unsigned long long *r);
  nvidia_gpu_nvml_ret_t (*pcie_replay_counter)(nvidia_gpu_nvml_dev_t d, unsigned int *v);
  nvidia_gpu_nvml_ret_t (*gpm_query_support)(nvidia_gpu_nvml_dev_t d,
                                             void *sup); /* nvmlGpmSupport_t */
  nvidia_gpu_nvml_ret_t (*gpm_sample_alloc)(nvidia_gpu_nvml_sample_t *s);
  nvidia_gpu_nvml_ret_t (*gpm_sample_free)(nvidia_gpu_nvml_sample_t s);
  nvidia_gpu_nvml_ret_t (*gpm_sample_get)(nvidia_gpu_nvml_dev_t d, nvidia_gpu_nvml_sample_t s);
  nvidia_gpu_nvml_ret_t (*gpm_metrics_get)(void *mg); /* nvmlGpmMetricsGet_t */
};

/* Load NVML (or the injected test API), init, and require GPM on every GPU; takes the first GPM
 * sample of each GPU. Returns 0 when the NVML path is ready, -1 otherwise (DCGM path). */
int nvidia_gpu_nvml_prepare(void);
/* GPUs on the NVML path (NVML index order = DCGM GPU id order, findings 26/27). */
int nvidia_gpu_nvml_ndev(void);
/* Fill row for NVML index idx at monotonic time now_us. Returns 0, or -1 when the GPM sample
 * failed (caller drops NVML and falls back to DCGM). Absent getters leave their fields 0. */
int nvidia_gpu_nvml_fill(int idx, long long now_us, dcgm_data_t *row);
void nvidia_gpu_nvml_cleanup(void);
const char *nvidia_gpu_nvml_last_error(void);
/* Tests: inject an API table (NULL restores the dlopen path). */
void nvidia_gpu_nvml_test_set_api(const struct nvidia_gpu_nvml_api *api);

#endif
