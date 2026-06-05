/* hwdetect — optional hardware stack probe via lspci; disables absent types. */
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <dirent.h>
#include <unistd.h>
#include <stdlib.h>
#include <time.h>
#include "stats.h"
#include "trace.h"

static void disable_type_if_present(const char *name)
{
  struct stats_type *type = stats_type_get(name);
  if (type != NULL)
    type->st_enabled = 0;
}

static void to_lower_ascii(char *s)
{
  while (*s != '\0') {
    *s = (char) tolower((unsigned char) *s);
    s++;
  }
}

/* lspci can omit the word "infiniband" for some Mellanox lines; class 0207 is InfiniBand. */
static int infiniband_sysfs_has_devices(void)
{
  DIR *d;
  struct dirent *ent;

  d = opendir("/sys/class/infiniband");
  if (d == NULL)
    return 0;
  while ((ent = readdir(d)) != NULL) {
    if (ent->d_name[0] == '.')
      continue;
    closedir(d);
    return 1;
  }
  closedir(d);
  return 0;
}

/*
 * Superchips / GH systems sometimes omit "nvidia" in lspci wording or only expose PCI IDs.
 * Prefer conservative positives over disabling GPU telemetry when the driver is present.
 */
static int sysfs_proc_indicates_nvidia_gpu(void)
{
  if (access("/proc/driver/nvidia/version", R_OK) == 0)
    return 1;
  if (access("/dev/nvidia0", F_OK) == 0)
    return 1;
  return 0;
}

static int lspci_line_nvidia_pci_gpu_device(const char *line)
{
  if (strstr(line, "[10de:") == NULL)
    return 0;
  return strstr(line, "[0300]") != NULL || strstr(line, "[0301]") != NULL
      || strstr(line, "[0302]") != NULL || strstr(line, "[0680]") != NULL
      || strstr(line, "[1202]") != NULL || strstr(line, "3d controller") != NULL
      || strstr(line, "vga compatible controller") != NULL
      || strstr(line, "display controller") != NULL
      || strstr(line, "processing accelerators") != NULL;
}

static int env_truthy(const char *name)
{
  const char *v = getenv(name);
  if (v == NULL)
    return 0;
  return strcmp(v, "1") == 0 || strcmp(v, "true") == 0 || strcmp(v, "true") == 0
      || strcmp(v, "yes") == 0 || strcmp(v, "yes") == 0 || strcmp(v, "on") == 0
      || strcmp(v, "on") == 0;
}

static int env_int_or_default(const char *name, int fallback)
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
  return (int) parsed;
}

static unsigned long g_nvidia_detect_miss_streak = 0;
struct hwdetect_probe_cache {
  int valid;
  long long cached_mono_us;
  int has_nvidia_gpu;
  int has_amd_gpu;
  int has_ib;
  int has_opa;
};
static struct hwdetect_probe_cache g_probe_cache;
static unsigned long g_probe_cache_hits;
static unsigned long g_probe_cache_misses;

static long long hwdetect_monotonic_us(void)
{
  struct timespec ts;

  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return -1;
  return (long long) ts.tv_sec * 1000000LL + (long long) ts.tv_nsec / 1000LL;
}

void hwdetect_reset_nvidia_disable_state(void)
{
  g_nvidia_detect_miss_streak = 0;
}

void hwdetect_invalidate_probe_cache(void)
{
  memset(&g_probe_cache, 0, sizeof(g_probe_cache));
}

int hwdetect_should_disable_nvidia_gpu(int has_nvidia_gpu)
{
  int miss_threshold = env_int_or_default("HPCPERFSTATS_NVIDIA_DISABLE_MISS_THRESHOLD", 1);
  if (has_nvidia_gpu) {
    if (g_nvidia_detect_miss_streak > 0) {
      TRACE("hwdetect: nvidia probe recovered after %lu miss(es)\n",
            g_nvidia_detect_miss_streak);
    }
    g_nvidia_detect_miss_streak = 0;
    return 0;
  }

  g_nvidia_detect_miss_streak++;
  if ((int) g_nvidia_detect_miss_streak < miss_threshold) {
    TRACE("hwdetect: nvidia miss streak %lu/%d; not disabling nvidia_gpu yet\n",
          g_nvidia_detect_miss_streak, miss_threshold);
    return 0;
  }
  return 1;
}

void hwdetect_probe_optional_stack_presence(int *has_nvidia_gpu,
                                            int *has_amd_gpu,
                                            int *has_ib,
                                            int *has_opa)
{
  long long now_mono_us = hwdetect_monotonic_us();
  int ttl_sec = env_int_or_default("HPCPERFSTATS_LSPCI_CACHE_TTL_SEC", 300);
  long long ttl_us = (long long) ttl_sec * 1000000LL;
  long long started_us = now_mono_us;
  long long elapsed_us = -1;
  int nvidia = 0;
  int amd = 0;
  int ib = 0;
  int opa = 0;
  FILE *fp = popen("lspci -nn 2>/dev/null", "r");
  char line[1024];

  if (g_probe_cache.valid && now_mono_us > 0 && ttl_us > 0
      && now_mono_us - g_probe_cache.cached_mono_us <= ttl_us) {
    g_probe_cache_hits++;
    if (has_nvidia_gpu != NULL)
      *has_nvidia_gpu = g_probe_cache.has_nvidia_gpu;
    if (has_amd_gpu != NULL)
      *has_amd_gpu = g_probe_cache.has_amd_gpu;
    if (has_ib != NULL)
      *has_ib = g_probe_cache.has_ib;
    if (has_opa != NULL)
      *has_opa = g_probe_cache.has_opa;
    return;
  }
  g_probe_cache_misses++;

  if (fp == NULL) {
    if (has_nvidia_gpu != NULL)
      *has_nvidia_gpu = sysfs_proc_indicates_nvidia_gpu();
    if (has_amd_gpu != NULL)
      *has_amd_gpu = 0;
    if (has_ib != NULL)
      *has_ib = infiniband_sysfs_has_devices();
    if (has_opa != NULL)
      *has_opa = 0;
    return;
  }

  while (fgets(line, sizeof(line), fp) != NULL) {
    to_lower_ascii(line);
    if (strstr(line, "vga compatible controller") != NULL ||
        strstr(line, "3d controller") != NULL ||
        strstr(line, "display controller") != NULL ||
        strstr(line, "processing accelerators") != NULL ||
        strstr(line, "accelerator") != NULL) {
      if (strstr(line, "nvidia") != NULL)
        nvidia = 1;
      if (strstr(line, "advanced micro devices") != NULL ||
          strstr(line, " amd/ati ") != NULL)
        amd = 1;
    }
    if (strstr(line, "infiniband") != NULL || strstr(line, "[0207]") != NULL)
      ib = 1;
    if (strstr(line, "omnipath") != NULL || strstr(line, "hfi") != NULL)
      opa = 1;
    if (!nvidia && lspci_line_nvidia_pci_gpu_device(line))
      nvidia = 1;
  }
  pclose(fp);

  if (!ib)
    ib = infiniband_sysfs_has_devices();

  if (!nvidia)
    nvidia = sysfs_proc_indicates_nvidia_gpu();

  if (has_nvidia_gpu != NULL)
    *has_nvidia_gpu = nvidia;
  if (has_amd_gpu != NULL)
    *has_amd_gpu = amd;
  if (has_ib != NULL)
    *has_ib = ib;
  if (has_opa != NULL)
    *has_opa = opa;

  if (now_mono_us > 0 && ttl_sec > 0) {
    g_probe_cache.valid = 1;
    g_probe_cache.cached_mono_us = now_mono_us;
    g_probe_cache.has_nvidia_gpu = nvidia;
    g_probe_cache.has_amd_gpu = amd;
    g_probe_cache.has_ib = ib;
    g_probe_cache.has_opa = opa;
  }
  if (started_us > 0) {
    elapsed_us = hwdetect_monotonic_us() - started_us;
    if (elapsed_us > 50000LL) {
      TRACE("hwdetect probe slow: elapsed_us=%lld cache_hits=%lu cache_misses=%lu\n",
            elapsed_us, g_probe_cache_hits, g_probe_cache_misses);
    }
  }
}

void auto_disable_optional_stats_by_lspci(void)
{
  int has_nvidia_gpu = 0;
  int has_amd_gpu = 0;
  int has_ib = 0;
  int has_opa = 0;

  hwdetect_probe_optional_stack_presence(&has_nvidia_gpu, &has_amd_gpu, &has_ib, &has_opa);
  if (env_truthy("HPCPERFSTATS_FORCE_NVIDIA_GPU")) {
    TRACE("hwdetect: HPCPERFSTATS_FORCE_NVIDIA_GPU is active; forcing nvidia_gpu enable\n");
    has_nvidia_gpu = 1;
  }

  if (hwdetect_should_disable_nvidia_gpu(has_nvidia_gpu)) {
    TRACE("hwdetect: disabling nvidia_gpu (probe did not detect NVIDIA GPU stack)\n");
    disable_type_if_present("nvidia_gpu");
  }
  if (!has_amd_gpu)
    disable_type_if_present("amd_gpu");
  if (!has_ib) {
    disable_type_if_present("host_ib");
    disable_type_if_present("host_ib_ext");
    disable_type_if_present("host_ib_sw");
  }
  if (!has_opa)
    disable_type_if_present("host_opa");
}
