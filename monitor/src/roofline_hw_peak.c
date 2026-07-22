/* Roofline peak schema emission (cadence-gated collect). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "stats.h"
#include "roofline_hw_peak.h"
#include "roofline_hw_peak_detect.h"

enum {
  CPU_PEAK_SOURCE_PROBED = 1,
  PEAK_CALC_VERSION_V2 = 2,
};

enum roofline_emit_mode {
  ROOFLINE_EMIT_CHANGEOVER = 0,
  ROOFLINE_EMIT_PERIODIC,
  ROOFLINE_EMIT_EVERY_SAMPLE
};

static unsigned long g_roofline_collect_calls;
static unsigned long g_roofline_design_skip_calls;
static struct roofline_cached_peaks g_roofline_cache;

static long long roofline_monotonic_us(void)
{
  struct timespec ts;

  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return -1;
  return (long long)ts.tv_sec * 1000000LL + (long long)ts.tv_nsec / 1000LL;
}

static int roofline_env_int_or_default(const char *name, int fallback)
{
  const char *v = getenv(name);
  char *end = NULL;
  long parsed;
  if (v == NULL || *v == '\0')
    return fallback;
  parsed = strtol(v, &end, 10);
  if (end == v || *end != '\0' || parsed <= 0)
    return fallback;
  if (parsed > 1000000L)
    return 1000000;
  return (int)parsed;
}

static enum roofline_emit_mode roofline_get_emit_mode(void)
{
  const char *mode = getenv("HPCPERFSTATS_ROOFLINE_MODE");
  if (mode == NULL || *mode == '\0' || strcmp(mode, "changeover") == 0)
    return ROOFLINE_EMIT_CHANGEOVER;
  if (strcmp(mode, "periodic") == 0)
    return ROOFLINE_EMIT_PERIODIC;
  if (strcmp(mode, "every_sample") == 0)
    return ROOFLINE_EMIT_EVERY_SAMPLE;
  return ROOFLINE_EMIT_CHANGEOVER;
}

static void roofline_hw_peak_collect(struct stats_type *type)
{
  struct stats *stats;
  enum roofline_emit_mode mode = roofline_get_emit_mode();
  int should_emit = 0;

  g_roofline_collect_calls++;

  /*
   * Emit host_roofline_peak only on collects that follow `stats_wr_hdr()` (same payload:
   * `$` banner / `!` schema lines, then the first timestamp block from `stats_buffer_collect`).
   * `stats_collect_on_changeover` is set only when `monitor_daemon_collect_to_ring(..., write_hdr=1, ...)`.
   */
  if (mode == ROOFLINE_EMIT_EVERY_SAMPLE) {
    should_emit = 1;
  } else if (mode == ROOFLINE_EMIT_PERIODIC) {
    int period_samples = roofline_env_int_or_default("HPCPERFSTATS_ROOFLINE_PERIOD_SAMPLES", 10);
    should_emit = stats_collect_on_changeover ||
                  (g_roofline_collect_calls % (unsigned long)period_samples) == 0;
  } else {
    should_emit = stats_collect_on_changeover;
  }

  if (!should_emit) {
    g_roofline_design_skip_calls++;
#ifdef DEBUG
    if ((g_roofline_design_skip_calls % 128UL) == 1UL) {
      fprintf(stderr, "host_roofline_peak: skipped by cadence mode=%d (changeover=%d, skips=%lu)\n",
              (int)mode, stats_collect_on_changeover, g_roofline_design_skip_calls);
    }
#endif
    return;
  }

  stats = get_current_stats(type, "-");
  if (stats == NULL)
    return;

  if (!g_roofline_cache.initialized) {
    long long started_us = roofline_monotonic_us();
    long long elapsed_us = -1;

    roofline_hw_peak_detect_fill_cache(&g_roofline_cache);

    if (started_us > 0) {
      elapsed_us = roofline_monotonic_us() - started_us;
      if (elapsed_us > 100000L) {
        fprintf(stderr, "host_roofline_peak: one-shot detect elapsed_us=%lld source=%llu\n",
                elapsed_us, g_roofline_cache.gpu_source);
      }
    }
  }

  stats_set(stats, "cpu_peak_fp64_flops_per_s", g_roofline_cache.cpu_flops);
  stats_set(stats, "cpu_peak_dram_bw_bytes_per_s", g_roofline_cache.cpu_bw);
  stats_set(stats, "cpu_peak_hbm_bw_bytes_per_s", g_roofline_cache.cpu_hbm_bw);
  stats_set(stats, "gpu_peak_fp64_flops_per_s", g_roofline_cache.gpu_flops);
  stats_set(stats, "gpu_peak_mem_bw_bytes_per_s", g_roofline_cache.gpu_mem_bw);
  stats_set(stats, "gpu_peak_io_link_bw_bytes_per_s", g_roofline_cache.gpu_io_bw);
  stats_set(stats, "cpu_peak_source", CPU_PEAK_SOURCE_PROBED);
  stats_set(stats, "gpu_peak_source", g_roofline_cache.gpu_source);
  stats_set(stats, "peak_calc_version", PEAK_CALC_VERSION_V2);
}

struct stats_type roofline_hw_peak_stats_type = {
    .st_collect = &roofline_hw_peak_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
    .st_name = "host_roofline_peak",
};
