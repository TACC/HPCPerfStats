/* amd_gpu - AMD GPU metrics via GPUPerfAPI (schema-compatible stub when unavailable). */
#include <dlfcn.h>
#include <stddef.h>
#include "amd_gpu.h"
#include "stats.h"
#include "trace.h"

typedef int (*gpa_init_fn_t)(void);

static int try_gpa_initialize(void)
{
  static const char *libs[] = {"libGPUPerfAPICounters.so", "libGPUPerfAPI.so", NULL};
  int i;

  for (i = 0; libs[i] != NULL; i++) {
    void *h = dlopen(libs[i], RTLD_LAZY | RTLD_LOCAL);

    if (h != NULL) {
      gpa_init_fn_t init_fn = (gpa_init_fn_t)dlsym(h, "GpaInitialize");

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

void amd_gpu_publish_not_built(struct stats *stats)
{
  if (stats == NULL)
    return;
  stats_set(stats, "gpu_count", 1);
  stats_set_missing_all(stats, STATS_SENTINEL_NOT_BUILT);
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

  amd_gpu_publish_not_built(stats);
}

struct stats_type amd_gpu_stats_type = {
    .st_collect = &amd_gpu_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
    .st_name = "amd_gpu",
};
