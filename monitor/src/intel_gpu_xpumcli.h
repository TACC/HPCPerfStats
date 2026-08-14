/* intel_gpu_xpumcli — short-lived xpumcli child for PVC intel_gpu (no in-process xpumInit). */
#ifndef INTEL_GPU_XPUMCLI_H
#define INTEL_GPU_XPUMCLI_H

#include <stddef.h>

struct stats;
struct stats_type;

/* Dump metric set for KEYS parity (xpumcli dump -m IDs, not XPUM_STATS_*). */
#define INTEL_GPU_XPUMCLI_DUMP_METRICS "0,1,2,3,4,5,9,17,18,35"

#define INTEL_GPU_XPUMCLI_MAX_DEVICES 32
#define INTEL_GPU_XPUMCLI_CAPTURE_TIMEOUT_MS 8000

struct intel_gpu_xpumcli_sample {
  int device_id;
  int has_gpu_util;
  int has_power;
  int has_temp;
  int has_mem_util;
  int has_mem_used_mb;
  int has_freq;
  int has_eu_active;
  int has_mem_bw;
  int has_throttle;
  double gpu_util;
  double power_w;
  double temp_c;
  double mem_util;
  double mem_used_mb;
  double freq_mhz;
  double eu_active;
  double mem_bw;
  unsigned long long throttle_flags;
};

/*! Parse `xpumcli discovery` text; fill device_ids[0..*out_count). Returns 0 on success. */
int intel_gpu_xpumcli_parse_discovery(const char *text, int *device_ids, int max_devices,
                                      int *out_count);

/*! Parse `xpumcli dump` CSV; fill samples for known DeviceId rows. Returns sample count. */
int intel_gpu_xpumcli_parse_dump_csv(const char *text, struct intel_gpu_xpumcli_sample *samples,
                                     int max_samples);

/*! Publish one sample into collector stats (PCIe/Xe left 0 on xpumcli path). */
void intel_gpu_xpumcli_publish_sample(struct stats *stats, const struct intel_gpu_xpumcli_sample *s,
                                      int gpu_count);

/*! Collect via xpumcli discovery+dump. Returns 0 if any device published. */
int intel_gpu_xpumcli_collect(struct stats_type *type);

#ifdef INTEL_GPU_TEST_BUILD
typedef int (*intel_gpu_xpumcli_capture_fn)(char *const argv[], char *out, size_t out_cap);
void intel_gpu_xpumcli_test_set_capture(intel_gpu_xpumcli_capture_fn fn);
void intel_gpu_xpumcli_test_reset(void);
#endif

#endif /* INTEL_GPU_XPUMCLI_H */
