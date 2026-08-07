/* One-shot CPU/GPU roofline peak detection (sysfs, NVML, vendor tools). */
#include <ctype.h>
#include <dirent.h>
#include <dlfcn.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "stats.h"
#include "host_edac_mem_topology.h"
#include "roofline_hw_peak_detect.h"
#include "roofline_hw_peak_identity.h"

static unsigned long long clamp_rate(double v)
{
  if (v <= 0.0)
    return 0ULL;
  if (v >= (double)ULLONG_MAX)
    return ULLONG_MAX;
  return (unsigned long long)(v + 0.5);
}
static int read_first_line(const char *path, char *buf, size_t cap)
{
  FILE *f = fopen(path, "re");
  if (f == NULL)
    return -1;
  if (fgets(buf, (int)cap, f) == NULL) {
    fclose(f);
    return -1;
  }
  fclose(f);
  return 0;
}

static int roofline_path_join2(char *dst, size_t cap, const char *a, const char *b)
{
  int n;
  n = snprintf(dst, cap, "%s/%s", a, b);
  return (n > 0 && (size_t)n < cap) ? 0 : -1;
}

static int roofline_path_join3(char *dst, size_t cap, const char *a, const char *b, const char *c)
{
  int n;
  n = snprintf(dst, cap, "%s/%s/%s", a, b, c);
  return (n > 0 && (size_t)n < cap) ? 0 : -1;
}

static int read_long_long_from_file(const char *path, long long *out)
{
  char line[256];
  char *end = NULL;
  long long v;

  if (read_first_line(path, line, sizeof(line)) != 0)
    return -1;
  v = strtoll(line, &end, 10);
  if (end == line)
    return -1;
  *out = v;
  return 0;
}

static int line_contains_token(const char *line, const char *token)
{
  const char *p = line;
  size_t n = strlen(token);
  while (*p != '\0') {
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
      p++;
    if (*p == '\0')
      break;
    if (strncmp(p, token, n) == 0 && (p[n] == ' ' || p[n] == '\t' || p[n] == '\n' || p[n] == '\0'))
      return 1;
    while (*p != '\0' && *p != ' ' && *p != '\t' && *p != '\n')
      p++;
  }
  return 0;
}

static double detect_cpu_fp64_flops_per_cycle(void)
{
  FILE *f = fopen("/proc/cpuinfo", "re");
  char line[1024];
  int has_avx512 = 0, has_avx = 0, has_fma = 0, has_sse2 = 0, has_neon = 0, has_sve = 0;
  double flops;

  if (f == NULL)
    return 2.0;
  while (fgets(line, sizeof(line), f) != NULL) {
    if (strncmp(line, "flags", 5) == 0 || strncmp(line, "features", 8) == 0) {
      has_avx512 |= line_contains_token(line, "avx512f");
      has_avx |= line_contains_token(line, "avx2") || line_contains_token(line, "avx");
      has_fma |= line_contains_token(line, "fma") || line_contains_token(line, "asimdfhm");
      has_sse2 |= line_contains_token(line, "sse2");
      has_neon |= line_contains_token(line, "asimd") || line_contains_token(line, "neon");
      has_sve |= line_contains_token(line, "sve") || line_contains_token(line, "sve2");
      break;
    }
  }
  fclose(f);

  if (has_avx512)
    flops = has_fma ? 32.0 : 16.0;
  else if (has_avx)
    flops = has_fma ? 16.0 : 8.0;
  else if (has_sve)
    flops = 8.0;
  else if (has_neon)
    flops = 4.0;
  else if (has_sse2)
    flops = 4.0;
  else
    flops = 2.0;
  return flops;
}

static double detect_cpu_peak_flops_per_s(void)
{
  int i;
  double flops_per_cycle = detect_cpu_fp64_flops_per_cycle();
  double sum_hz = 0.0;

  for (i = 0; i < nr_cpus; i++) {
    char path[192];
    long long khz = 0;
    snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/base_frequency", i);
    if (read_long_long_from_file(path, &khz) != 0 || khz <= 0) {
      snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq", i);
      if (read_long_long_from_file(path, &khz) != 0 || khz <= 0) {
        snprintf(path, sizeof(path), "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_max_freq", i);
        if (read_long_long_from_file(path, &khz) != 0 || khz <= 0)
          khz = 0;
      }
    }
    if (khz > 0)
      sum_hz += (double)khz * 1000.0;
  }

  if (sum_hz <= 0.0) {
    FILE *f = fopen("/proc/cpuinfo", "re");
    char line[512];
    if (f != NULL) {
      while (fgets(line, sizeof(line), f) != NULL) {
        if (strncmp(line, "cpu MHz", 7) == 0) {
          char *p = strchr(line, ':');
          if (p != NULL) {
            double mhz = strtod(p + 1, NULL);
            if (mhz > 0.0)
              sum_hz += mhz * 1.0e6;
          }
        }
      }
      fclose(f);
    }
  }

  return sum_hz * flops_per_cycle;
}

static double parse_pcie_gtps(const char *s)
{
  while (*s != '\0' && !isdigit((unsigned char)*s))
    s++;
  if (*s == '\0')
    return 0.0;
  return strtod(s, NULL);
}

static double pcie_lane_bytes_per_s(double gtps)
{
  if (gtps >= 63.0)
    return 7.877e9;
  if (gtps >= 31.0)
    return 3.938e9;
  if (gtps >= 15.0)
    return 1.969e9;
  if (gtps >= 7.0)
    return 0.985e9;
  if (gtps >= 4.0)
    return 0.500e9;
  if (gtps >= 2.0)
    return 0.250e9;
  return 0.0;
}

static int parse_link_width(const char *s)
{
  while (*s != '\0' && !isdigit((unsigned char)*s))
    s++;
  if (*s == '\0')
    return 0;
  return (int)strtol(s, NULL, 10);
}

typedef struct {
  double *ddr_bw;
  double *hbm_bw;
} edac_bw_ctx_t;

static void sum_edac_dimm_bw(long long mtps, int is_hbm, void *ctx)
{
  edac_bw_ctx_t *bw = (edac_bw_ctx_t *)ctx;
  double dimm_bw = (double)mtps * 1000000.0 * 8.0;

  if (is_hbm) {
    if (bw->hbm_bw != NULL)
      *bw->hbm_bw += dimm_bw;
  } else if (bw->ddr_bw != NULL) {
    *bw->ddr_bw += dimm_bw;
  }
}

static void detect_cpu_peak_edac_bw_bytes_per_s(double *ddr_bw, double *hbm_bw)
{
  edac_bw_ctx_t ctx;

  if (ddr_bw != NULL)
    *ddr_bw = 0.0;
  if (hbm_bw != NULL)
    *hbm_bw = 0.0;
  ctx.ddr_bw = ddr_bw;
  ctx.hbm_bw = hbm_bw;
  (void)host_edac_foreach_dimm(sum_edac_dimm_bw, &ctx);
}

static double detect_cpu_peak_dram_bw_bytes_per_s(void)
{
  double ddr_bw = 0.0;
  double hbm_bw = 0.0;

  detect_cpu_peak_edac_bw_bytes_per_s(&ddr_bw, &hbm_bw);
  (void)hbm_bw;
  return ddr_bw;
}

static double parse_max_mhz_from_dpm_table(const char *path)
{
  FILE *f = fopen(path, "re");
  char line[256];
  double max_mhz = 0.0;
  if (f == NULL)
    return 0.0;
  while (fgets(line, sizeof(line), f) != NULL) {
    char *p = line;
    while (*p != '\0' && !isdigit((unsigned char)*p))
      p++;
    if (isdigit((unsigned char)*p)) {
      double mhz = strtod(p, NULL);
      if (mhz > max_mhz)
        max_mhz = mhz;
    }
  }
  fclose(f);
  return max_mhz;
}

static void detect_gpu_peaks_from_sysfs(double *flops, double *mem_bw, double *io_bw)
{
  DIR *dir = opendir("/sys/class/drm");
  struct dirent *ent;
  if (dir == NULL)
    return;
  while ((ent = readdir(dir)) != NULL) {
    char speed_path[256], width_path[256], speed_line[128], width_line[128], bw_path[256],
        mclk_path[256];
    double gtps;
    int width;
    long long mem_bw_attr = 0;
    double mclk_mhz = 0.0;
    if (strncmp(ent->d_name, "card", 4) != 0 || strchr(ent->d_name, '-') != NULL)
      continue;
    if (roofline_path_join3(speed_path, sizeof(speed_path), "/sys/class/drm", ent->d_name,
                            "device/max_link_speed") != 0)
      continue;
    if (roofline_path_join3(width_path, sizeof(width_path), "/sys/class/drm", ent->d_name,
                            "device/max_link_width") != 0)
      continue;
    if (roofline_path_join3(bw_path, sizeof(bw_path), "/sys/class/drm", ent->d_name,
                            "device/mem_info_max_bandwidth") != 0)
      continue;
    if (roofline_path_join3(mclk_path, sizeof(mclk_path), "/sys/class/drm", ent->d_name,
                            "device/pp_dpm_mclk") != 0)
      continue;

    if (read_long_long_from_file(bw_path, &mem_bw_attr) == 0 && mem_bw_attr > 0)
      *mem_bw += (double)mem_bw_attr;
    else {
      mclk_mhz = parse_max_mhz_from_dpm_table(mclk_path);
      if (mclk_mhz > 0.0) {
        /* If bus-width is unavailable, keep probe-driven estimate conservative at 0. */
      }
    }

    if (read_first_line(speed_path, speed_line, sizeof(speed_line)) != 0)
      continue;
    if (read_first_line(width_path, width_line, sizeof(width_line)) != 0)
      continue;
    gtps = parse_pcie_gtps(speed_line);
    width = parse_link_width(width_line);
    if (gtps > 0.0 && width > 0)
      *io_bw += pcie_lane_bytes_per_s(gtps) * (double)width;
  }
  closedir(dir);
  (void)flops;
}

static unsigned int detect_cpu_part_from_proc(void)
{
  FILE *f = fopen("/proc/cpuinfo", "re");
  char line[256];
  unsigned int part = 0;

  if (f == NULL)
    return 0;
  while (fgets(line, sizeof(line), f) != NULL) {
    if (strncmp(line, "CPU part", 8) == 0) {
      char *p = strchr(line, ':');
      if (p != NULL) {
        part = (unsigned int)strtoul(p + 1, NULL, 0);
        break;
      }
    }
  }
  fclose(f);
  return part;
}

/* Confirmed fields on Horizon: name,clocks.max.sm,compute_cap (+ SM-count identity). */
static double detect_nvidia_fp64_peak_via_nvidia_smi(void)
{
  FILE *fp = popen("nvidia-smi --query-gpu=name,clocks.max.sm,compute_cap "
                   "--format=csv,noheader,nounits 2>/dev/null",
                   "r");
  char line[768];
  double total = 0.0;

  if (fp == NULL)
    return 0.0;

  while (fgets(line, sizeof(line), fp) != NULL) {
    char name[256];
    double sm_mhz = 0.0;
    int maj = 0, min = 0;
    int sm_count;
    int cores_per_sm;
    double ratio;

    if (roofline_parse_smi_flops_line(line, name, sizeof(name), &sm_mhz, &maj, &min) != 0)
      continue;
    sm_count = roofline_nvidia_sm_count_from_name(name);
    if (sm_count <= 0)
      continue;
    cores_per_sm = roofline_nvidia_cuda_cores_per_sm(maj, min, name);
    ratio = roofline_nvidia_fp64_ratio_from_cc(maj, min, name);
    total += roofline_nvidia_fp64_flops_from_sm(sm_count, cores_per_sm, sm_mhz, ratio);
  }
  pclose(fp);
  return total;
}

/*
 * SMI mem/PCIe: allowlisted HBM × GPU count, PCIe gen×width, and GH200 C2C sum.
 * c2c_bw is separate so detect can prefer C2C over misleading DRM PCIe x1.
 */
static void detect_nvidia_mem_io_via_smi(double *mem_bw, double *io_bw, double *c2c_bw,
                                         int *used_identity_mem)
{
  FILE *fp;
  char line[768];

  if (mem_bw == NULL || io_bw == NULL)
    return;
  if (c2c_bw != NULL)
    *c2c_bw = 0.0;
  if (used_identity_mem != NULL)
    *used_identity_mem = 0;

  fp = popen("nvidia-smi --query-gpu=name,memory.total,clocks.max.memory,"
             "pcie.link.gen.max,pcie.link.width.max "
             "--format=csv,noheader,nounits 2>/dev/null",
             "r");
  if (fp == NULL)
    return;

  while (fgets(line, sizeof(line), fp) != NULL) {
    char name[256];
    double mem_mib = 0.0, mem_mhz = 0.0;
    int gen = 0, width = 0;
    double hbm;
    double c2c;
    double lane;

    if (roofline_parse_smi_mem_pcie_line(line, name, sizeof(name), &mem_mib, &mem_mhz, &gen,
                                         &width) != 0)
      continue;
    (void)mem_mhz;
    lane = roofline_pcie_gen_lane_bytes_per_s(gen);
    if (lane > 0.0 && width > 0)
      *io_bw += lane * (double)width;
    hbm = roofline_nvidia_hbm_bw_from_name_mem(name, mem_mib);
    if (hbm > 0.0) {
      *mem_bw += hbm;
      if (used_identity_mem != NULL)
        *used_identity_mem = 1;
    }
    c2c = roofline_nvidia_c2c_bw_from_name(name);
    if (c2c > 0.0 && c2c_bw != NULL)
      *c2c_bw += c2c;
  }
  pclose(fp);
}

static double detect_nvidia_fp64_peak_via_nvml(void)
{
  typedef int nvmlReturn_t;
  typedef struct nvmlDevice_st *nvmlDevice_t;
  typedef nvmlReturn_t (*fn_nvmlInit_v2)(void);
  typedef nvmlReturn_t (*fn_nvmlShutdown)(void);
  typedef nvmlReturn_t (*fn_nvmlDeviceGetCount_v2)(unsigned int *);
  typedef nvmlReturn_t (*fn_nvmlDeviceGetHandleByIndex_v2)(unsigned int, nvmlDevice_t *);
  typedef nvmlReturn_t (*fn_nvmlDeviceGetName)(nvmlDevice_t, char *, unsigned int);
  typedef nvmlReturn_t (*fn_nvmlDeviceGetCudaComputeCapability)(nvmlDevice_t, int *, int *);
  typedef nvmlReturn_t (*fn_nvmlDeviceGetMultiProcessorCount)(nvmlDevice_t, unsigned int *);
  typedef nvmlReturn_t (*fn_nvmlDeviceGetMaxClockInfo)(nvmlDevice_t, unsigned int, unsigned int *);

  enum { NVML_SUCCESS = 0, NVML_CLOCK_SM = 1 };

  void *lib = dlopen("libnvidia-ml.so.1", RTLD_LAZY);
  fn_nvmlInit_v2 p_nvmlInit_v2;
  fn_nvmlShutdown p_nvmlShutdown;
  fn_nvmlDeviceGetCount_v2 p_nvmlDeviceGetCount_v2;
  fn_nvmlDeviceGetHandleByIndex_v2 p_nvmlDeviceGetHandleByIndex_v2;
  fn_nvmlDeviceGetName p_nvmlDeviceGetName;
  fn_nvmlDeviceGetCudaComputeCapability p_nvmlDeviceGetCudaComputeCapability;
  fn_nvmlDeviceGetMultiProcessorCount p_nvmlDeviceGetMultiProcessorCount;
  fn_nvmlDeviceGetMaxClockInfo p_nvmlDeviceGetMaxClockInfo;
  double total = 0.0;
  unsigned int count = 0;
  unsigned int i;

  if (lib == NULL)
    return 0.0;

  p_nvmlInit_v2 = (fn_nvmlInit_v2)dlsym(lib, "nvmlInit_v2");
  p_nvmlShutdown = (fn_nvmlShutdown)dlsym(lib, "nvmlShutdown");
  p_nvmlDeviceGetCount_v2 = (fn_nvmlDeviceGetCount_v2)dlsym(lib, "nvmlDeviceGetCount_v2");
  p_nvmlDeviceGetHandleByIndex_v2 =
      (fn_nvmlDeviceGetHandleByIndex_v2)dlsym(lib, "nvmlDeviceGetHandleByIndex_v2");
  p_nvmlDeviceGetName = (fn_nvmlDeviceGetName)dlsym(lib, "nvmlDeviceGetName");
  p_nvmlDeviceGetCudaComputeCapability =
      (fn_nvmlDeviceGetCudaComputeCapability)dlsym(lib, "nvmlDeviceGetCudaComputeCapability");
  p_nvmlDeviceGetMultiProcessorCount =
      (fn_nvmlDeviceGetMultiProcessorCount)dlsym(lib, "nvmlDeviceGetMultiProcessorCount");
  p_nvmlDeviceGetMaxClockInfo =
      (fn_nvmlDeviceGetMaxClockInfo)dlsym(lib, "nvmlDeviceGetMaxClockInfo");

  if (p_nvmlInit_v2 == NULL || p_nvmlShutdown == NULL || p_nvmlDeviceGetCount_v2 == NULL ||
      p_nvmlDeviceGetHandleByIndex_v2 == NULL || p_nvmlDeviceGetName == NULL ||
      p_nvmlDeviceGetCudaComputeCapability == NULL || p_nvmlDeviceGetMultiProcessorCount == NULL ||
      p_nvmlDeviceGetMaxClockInfo == NULL) {
    dlclose(lib);
    return 0.0;
  }

  if (p_nvmlInit_v2() != NVML_SUCCESS) {
    dlclose(lib);
    return 0.0;
  }
  if (p_nvmlDeviceGetCount_v2(&count) != NVML_SUCCESS) {
    p_nvmlShutdown();
    dlclose(lib);
    return 0.0;
  }

  for (i = 0; i < count; i++) {
    nvmlDevice_t dev;
    char name[96];
    int cc_major = 0, cc_minor = 0;
    unsigned int sm_count = 0;
    unsigned int sm_mhz = 0;
    int cores_per_sm;
    double fp64_ratio;

    if (p_nvmlDeviceGetHandleByIndex_v2(i, &dev) != NVML_SUCCESS)
      continue;
    if (p_nvmlDeviceGetName(dev, name, (unsigned int)sizeof(name)) != NVML_SUCCESS)
      snprintf(name, sizeof(name), "%s", "unknown");
    if (p_nvmlDeviceGetCudaComputeCapability(dev, &cc_major, &cc_minor) != NVML_SUCCESS)
      continue;
    if (p_nvmlDeviceGetMultiProcessorCount(dev, &sm_count) != NVML_SUCCESS)
      continue;
    if (p_nvmlDeviceGetMaxClockInfo(dev, NVML_CLOCK_SM, &sm_mhz) != NVML_SUCCESS)
      continue;

    cores_per_sm = roofline_nvidia_cuda_cores_per_sm(cc_major, cc_minor, name);
    fp64_ratio = roofline_nvidia_fp64_ratio_from_cc(cc_major, cc_minor, name);
    total +=
        roofline_nvidia_fp64_flops_from_sm((int)sm_count, cores_per_sm, (double)sm_mhz, fp64_ratio);
  }

  p_nvmlShutdown();
  dlclose(lib);
  return total;
}

static double amd_fp64_ratio_from_gfx(const char *gfx)
{
  if (strstr(gfx, "gfx90a") != NULL || strstr(gfx, "gfx940") != NULL ||
      strstr(gfx, "gfx941") != NULL || strstr(gfx, "gfx942") != NULL ||
      strstr(gfx, "gfx908") != NULL || strstr(gfx, "gfx906") != NULL)
    return 0.5;
  return 1.0 / 16.0;
}

static double detect_amd_fp64_peak_via_rocminfo(void)
{
  FILE *fp = popen("rocminfo 2>/dev/null", "r");
  char line[512];
  char gfx[64] = "";
  int cu = 0;
  double mhz = 0.0;
  double total = 0.0;

  if (fp == NULL)
    return 0.0;
  while (fgets(line, sizeof(line), fp) != NULL) {
    if (strstr(line, "name:") != NULL && strstr(line, "gfx") != NULL) {
      char *g = strstr(line, "gfx");
      if (g != NULL) {
        size_t n = 0;
        while (g[n] != '\0' && !isspace((unsigned char)g[n]) && n + 1 < sizeof(gfx))
          n++;
        memcpy(gfx, g, n);
        gfx[n] = '\0';
      }
    } else if (strstr(line, "Compute Unit:") != NULL) {
      char *p = strchr(line, ':');
      if (p != NULL)
        cu = (int)strtol(p + 1, NULL, 10);
    } else if (strstr(line, "Max Clock Freq. (MHz):") != NULL) {
      char *p = strchr(line, ':');
      if (p != NULL)
        mhz = strtod(p + 1, NULL);
    } else if (line[0] == '\n' || line[0] == '\r') {
      if (cu > 0 && mhz > 0.0 && gfx[0] != '\0') {
        double ratio = amd_fp64_ratio_from_gfx(gfx);
        total += (double)cu * 128.0 * ratio * (mhz * 1.0e6);
      }
      cu = 0;
      mhz = 0.0;
      gfx[0] = '\0';
    }
  }
  if (cu > 0 && mhz > 0.0 && gfx[0] != '\0') {
    double ratio = amd_fp64_ratio_from_gfx(gfx);
    total += (double)cu * 128.0 * ratio * (mhz * 1.0e6);
  }
  pclose(fp);
  return total;
}

static double detect_gpu_fp64_peak_vendor_runtime(unsigned long long *src_out)
{
  double flops = detect_nvidia_fp64_peak_via_nvml();
  if (flops > 0.0) {
    if (src_out != NULL)
      *src_out = ROOFLINE_GPU_PEAK_SOURCE_VENDOR_NVML;
    return flops;
  }
#ifdef DEBUG
  {
    static int logged_nvml_fail;
    if (!logged_nvml_fail) {
      logged_nvml_fail = 1;
      fprintf(stderr, "host_roofline_peak: NVML FP64 peak init/query failed or zero; trying smi\n");
    }
  }
#endif
  flops = detect_nvidia_fp64_peak_via_nvidia_smi();
  if (flops > 0.0) {
    if (src_out != NULL)
      *src_out = ROOFLINE_GPU_PEAK_SOURCE_VENDOR_SMI;
    return flops;
  }
  flops = detect_amd_fp64_peak_via_rocminfo();
  if (flops > 0.0 && src_out != NULL)
    *src_out = ROOFLINE_GPU_PEAK_SOURCE_VENDOR_SMI;
  return flops;
}

void roofline_hw_peak_detect_fill_cache(struct roofline_cached_peaks *cache)
{
  double cpu_flops = 0.0;
  double cpu_bw = 0.0;
  double cpu_hbm_bw = 0.0;
  double gpu_flops = 0.0;
  double gpu_mem_bw = 0.0;
  double gpu_io_bw = 0.0;
  unsigned long long gpu_source = ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN;
  unsigned long long cpu_source = ROOFLINE_CPU_PEAK_SOURCE_FAIL_OPEN;
  int used_identity_mem = 0;

  if (cache == NULL)
    return;
  if (getenv("HPCPERFSTATS_SKIP_HW_PROBE") != NULL) {
    cpu_flops = (nr_cpus > 0) ? (double)nr_cpus * 1.0e9 : 1.0e9;
    cpu_bw = 1.0e9;
    cpu_hbm_bw = 0.0;
    cpu_source = ROOFLINE_CPU_PEAK_SOURCE_PROBED;
  } else {
    cpu_flops = detect_cpu_peak_flops_per_s();
    detect_cpu_peak_edac_bw_bytes_per_s(&cpu_bw, &cpu_hbm_bw);
    if (cpu_bw > 0.0 || cpu_hbm_bw > 0.0 || cpu_flops > 0.0)
      cpu_source = ROOFLINE_CPU_PEAK_SOURCE_PROBED;
    if (cpu_bw <= 0.0) {
      double grace_bw = roofline_grace_dram_bw_from_cpu_part(detect_cpu_part_from_proc());
      if (grace_bw > 0.0) {
        cpu_bw = grace_bw;
        cpu_source = ROOFLINE_CPU_PEAK_SOURCE_IDENTITY;
      }
    }
    detect_gpu_peaks_from_sysfs(&gpu_flops, &gpu_mem_bw, &gpu_io_bw);
    if (gpu_mem_bw > 0.0 || gpu_io_bw > 0.0)
      gpu_source = ROOFLINE_GPU_PEAK_SOURCE_PROBED;
    if (gpu_flops <= 0.0) {
      unsigned long long vendor_src = ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN;
      gpu_flops = detect_gpu_fp64_peak_vendor_runtime(&vendor_src);
      if (gpu_flops > 0.0 && vendor_src != ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN &&
          (gpu_source == ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN ||
           gpu_source == ROOFLINE_GPU_PEAK_SOURCE_PROBED))
        gpu_source = vendor_src;
    }
    {
      double smi_mem = 0.0;
      double smi_io = 0.0;
      double smi_c2c = 0.0;

      detect_nvidia_mem_io_via_smi(&smi_mem, &smi_io, &smi_c2c, &used_identity_mem);
      if (gpu_mem_bw <= 0.0 && smi_mem > 0.0)
        gpu_mem_bw = smi_mem;
      if (gpu_io_bw <= 0.0 && smi_io > 0.0) {
        gpu_io_bw = smi_io;
        if (gpu_source == ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN)
          gpu_source = ROOFLINE_GPU_PEAK_SOURCE_VENDOR_SMI;
      }
      /* GH200: published C2C beats misleading DRM/smi PCIe x1. */
      if (smi_c2c > gpu_io_bw) {
        gpu_io_bw = smi_c2c;
        if (gpu_source == ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN ||
            gpu_source == ROOFLINE_GPU_PEAK_SOURCE_PROBED)
          gpu_source = ROOFLINE_GPU_PEAK_SOURCE_IDENTITY;
      }
      if (used_identity_mem && gpu_source == ROOFLINE_GPU_PEAK_SOURCE_FAIL_OPEN)
        gpu_source = ROOFLINE_GPU_PEAK_SOURCE_IDENTITY;
    }
  }
  cache->cpu_flops = clamp_rate(cpu_flops);
  cache->cpu_bw = clamp_rate(cpu_bw);
  cache->cpu_hbm_bw = clamp_rate(cpu_hbm_bw);
  cache->gpu_flops = clamp_rate(gpu_flops);
  cache->gpu_mem_bw = clamp_rate(gpu_mem_bw);
  cache->gpu_io_bw = clamp_rate(gpu_io_bw);
  cache->gpu_source = gpu_source;
  cache->cpu_source = cpu_source;
  cache->initialized = 1;
}
