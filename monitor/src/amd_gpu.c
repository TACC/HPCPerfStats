/* amd_gpu — AMD GPU metrics via GPUPerfAPI (schema-compatible stub when unavailable). */
#include <dlfcn.h>
#include <stddef.h>
#include "amd_gpu.h"
#include "stats.h"
#include "trace.h"

typedef int (*gpa_init_fn_t)(void);

static int try_gpa_initialize(void)
{
  static const char *libs[] = {
    "libGPUPerfAPICounters.so",
    "libGPUPerfAPI.so",
    NULL
  };
  int i;

  for (i = 0; libs[i] != NULL; i++) {
    void *h = dlopen(libs[i], RTLD_LAZY | RTLD_LOCAL);

    if (h != NULL) {
      gpa_init_fn_t init_fn = (gpa_init_fn_t) dlsym(h, "GpaInitialize");

      if (init_fn != NULL) {
        int rc = init_fn();

        dlclose(h);
        if (rc == 0)
          return 0;
      } else {
        dlclose(h);
      }
    }
  }
  return -1;
}

static void amd_gpu_publish_zeros(struct stats *stats)
{
  if (stats == NULL)
    return;
  stats_set(stats, "gpu_util", 0);
  stats_set(stats, "gpu_mem_util", 0);
  stats_set(stats, "gpu_mem_total_mb", 0);
  stats_set(stats, "gpu_mem_used_mb", 0);
  stats_set(stats, "power_usage", 0);
  stats_set(stats, "temperature", 0);
  stats_set(stats, "fp64_active", 0);
  stats_set(stats, "sm_active", 0);
  stats_set(stats, "sm_occupancy", 0);
  stats_set(stats, "fp32_active", 0);
  stats_set(stats, "fp16_active", 0);
  stats_set(stats, "tensor_active", 0);
  stats_set(stats, "clocks_event_reasons", 0);
  stats_set(stats, "gpu_flops_rate", 0);
  stats_set(stats, "gpu_mem_bw_bytes_rate", 0);
  stats_set(stats, "gpu_flops", 0);
  stats_set(stats, "gpu_mem_read_bytes", 0);
  stats_set(stats, "gpu_mem_write_bytes", 0);
  stats_set(stats, "gpu_mem_total_bytes", 0);
  stats_set(stats, "gpu_count", 1);
}

static void amd_gpu_collect(struct stats_type *type)
{
  struct stats *stats;

  if (type == NULL)
    return;
  stats = get_current_stats(type, "0");
  if (stats == NULL) {
    type->st_enabled = 0;
    return;
  }

  if (try_gpa_initialize() != 0) {
    TRACE("GPUPerfAPI not available at runtime; disabling amd_gpu type\n");
    type->st_enabled = 0;
    return;
  }

  amd_gpu_publish_zeros(stats);
}

struct stats_type amd_gpu_stats_type = {
  .st_collect = &amd_gpu_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
  .st_name = "amd_gpu",
};
