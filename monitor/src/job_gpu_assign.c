/* Map Slurm GPU grants and open device fds to collector GPU dev strings. */
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "job_gpu_assign.h"
#include "job_proc_discover.h"
#include "monitor_log.h"

static enum job_gpu_autodetect g_ad_cached = JOB_GPU_AD_UNKNOWN;
static int g_ad_have_cache;
static char *g_gres_conf_override;
static job_gpu_nvml_index_fn g_nvml_index_fn;
static job_gpu_minor_fn g_minor_fn;
static int g_field52_warned;

void job_gpu_assign_set_gres_conf_path(const char *path)
{
  free(g_gres_conf_override);
  g_gres_conf_override = NULL;
  if (path != NULL && path[0] != '\0')
    g_gres_conf_override = strdup(path);
  g_ad_have_cache = 0;
}

void job_gpu_assign_reset_autodetect_cache(void)
{
  g_ad_have_cache = 0;
  g_ad_cached = JOB_GPU_AD_UNKNOWN;
}

void job_gpu_assign_set_nvml_hooks(job_gpu_nvml_index_fn idx_fn, job_gpu_minor_fn minor_fn)
{
  g_nvml_index_fn = idx_fn;
  g_minor_fn = minor_fn;
}

static enum job_gpu_autodetect parse_autodetect_token(const char *v)
{
  if (v == NULL)
    return JOB_GPU_AD_UNKNOWN;
  if (strcmp(v, "nvml") == 0)
    return JOB_GPU_AD_NVML;
  if (strcmp(v, "nvidia") == 0)
    return JOB_GPU_AD_NVIDIA;
  if (strcmp(v, "rsmi") == 0)
    return JOB_GPU_AD_RSMI;
  if (strcmp(v, "oneapi") == 0)
    return JOB_GPU_AD_ONEAPI;
  if (strcmp(v, "full") == 0)
    return JOB_GPU_AD_FULL;
  if (strcmp(v, "off") == 0)
    return JOB_GPU_AD_OFF;
  return JOB_GPU_AD_UNKNOWN;
}

static void resolve_gres_conf_path(char *out, size_t out_cap)
{
  const char *slurm_conf;

  if (g_gres_conf_override != NULL) {
    snprintf(out, out_cap, "%s", g_gres_conf_override);
    return;
  }
  slurm_conf = getenv("SLURM_CONF");
  if (slurm_conf != NULL && slurm_conf[0] == '/') {
    const char *slash = strrchr(slurm_conf, '/');

    /* A truncated path would name the wrong file: fall back to the default instead. */
    if (slash != NULL && slash - slurm_conf < INT_MAX &&
        snprintf(out, out_cap, "%.*s/gres.conf", (int)(slash - slurm_conf), slurm_conf) <
            (int)out_cap)
      return;
  }
  snprintf(out, out_cap, "/etc/slurm/gres.conf");
}

static void short_hostname(char *out, size_t out_cap)
{
  char host[HOST_NAME_MAX + 1];
  char *dot;

  if (gethostname(host, sizeof(host)) != 0) {
    snprintf(out, out_cap, "%s", "");
    return;
  }
  host[sizeof(host) - 1] = '\0';
  dot = strchr(host, '.');
  if (dot)
    *dot = '\0';
  snprintf(out, out_cap, "%s", host);
}

enum job_gpu_autodetect job_gpu_assign_autodetect(void)
{
  char path[PATH_MAX];
  char host[128];
  FILE *f;
  char line[512];
  enum job_gpu_autodetect global_ad = JOB_GPU_AD_OFF;
  enum job_gpu_autodetect node_ad = JOB_GPU_AD_UNKNOWN;

  if (g_ad_have_cache)
    return g_ad_cached;

  resolve_gres_conf_path(path, sizeof(path));
  short_hostname(host, sizeof(host));
  f = fopen(path, "re");
  if (f == NULL) {
    g_ad_cached = JOB_GPU_AD_UNKNOWN;
    g_ad_have_cache = 1;
    return g_ad_cached;
  }
  while (fgets(line, sizeof(line), f) != NULL) {
    char *p = line;
    char *ad;
    char *eq;
    char nodename[128];

    while (isspace((unsigned char)*p))
      p++;
    if (*p == '\0' || *p == '#')
      continue;
    ad = strstr(p, "AutoDetect=");
    if (ad == NULL)
      continue;
    eq = ad + strlen("AutoDetect=");
    {
      char tok[32];
      size_t ti = 0;

      while (eq[ti] && !isspace((unsigned char)eq[ti]) && eq[ti] != ',' && ti + 1 < sizeof(tok)) {
        tok[ti] = eq[ti];
        ti++;
      }
      tok[ti] = '\0';
      if (strncmp(p, "NodeName=", 9) == 0) {
        /* NodeName=foo AutoDetect=... */
        if (sscanf(p, "NodeName=%127s", nodename) == 1) {
          char *comma = strchr(nodename, ',');

          if (comma)
            *comma = '\0';
          if (host[0] && strcmp(nodename, host) == 0)
            node_ad = parse_autodetect_token(tok);
        }
      } else {
        global_ad = parse_autodetect_token(tok);
      }
    }
  }
  fclose(f);
  g_ad_cached = (node_ad != JOB_GPU_AD_UNKNOWN) ? node_ad : global_ad;
  g_ad_have_cache = 1;
  return g_ad_cached;
}

static int set_add_dev(struct job_gpu_set *set, const char *dev)
{
  size_t i;

  if (set == NULL || dev == NULL || dev[0] == '\0')
    return -1;
  for (i = 0; i < set->n; i++) {
    if (strcmp(set->devs[i], dev) == 0)
      return 0;
  }
  if (set->n >= JOB_GPU_DEV_MAX)
    return -1;
  snprintf(set->devs[set->n], JOB_GPU_DEV_STR, "%s", dev);
  set->n++;
  return 0;
}

static int cmp_dev(const void *a, const void *b)
{
  return strcmp((const char *)a, (const char *)b);
}

static void set_sort_unique(struct job_gpu_set *set)
{
  size_t i;
  size_t w = 0;

  if (set == NULL || set->n < 2)
    return;
  qsort(set->devs, set->n, JOB_GPU_DEV_STR, cmp_dev);
  for (i = 0; i < set->n; i++) {
    if (w == 0 || strcmp(set->devs[w - 1], set->devs[i]) != 0) {
      if (w != i)
        memcpy(set->devs[w], set->devs[i], JOB_GPU_DEV_STR);
      w++;
    }
  }
  set->n = w;
}

static int map_nvml_index_to_dev(int v, char *dev_out, size_t cap)
{
  int i;

  if (g_nvml_index_fn != NULL) {
    i = g_nvml_index_fn(v);
    if (i < 0)
      return -1;
    snprintf(dev_out, cap, "%d", i);
    return 0;
  }
  /* Production without hook: identity (field 52 lookup deferred to nvidia helper). */
  if (v < 0)
    return -1;
  snprintf(dev_out, cap, "%d", v);
  return 0;
}

int job_gpu_assign_map_grant(const char *slurm_job_gpus, struct job_gpu_set *granted)
{
  enum job_gpu_autodetect ad;
  const char *p;
  char *end;

  if (granted == NULL)
    return -1;
  memset(granted, 0, sizeof(*granted));
  ad = job_gpu_assign_autodetect();
  if (ad == JOB_GPU_AD_OFF || ad == JOB_GPU_AD_UNKNOWN)
    return 0;
  if (slurm_job_gpus == NULL || slurm_job_gpus[0] == '\0')
    return 0;

  p = slurm_job_gpus;
  while (*p) {
    long v;
    char dev[JOB_GPU_DEV_STR];

    while (*p == ',' || isspace((unsigned char)*p))
      p++;
    if (*p == '\0')
      break;
    errno = 0;
    v = strtol(p, &end, 10);
    /* v > INT_MAX would not fit a JOB_GPU_DEV_STR slot: skip, do not truncate. */
    if (end == p || errno != 0 || v < 0 || v > INT_MAX) {
      /* skip non-decimal token */
      while (*p && *p != ',')
        p++;
      continue;
    }
    p = end;
    if (ad == JOB_GPU_AD_NVML || ad == JOB_GPU_AD_NVIDIA || ad == JOB_GPU_AD_FULL) {
      if (map_nvml_index_to_dev((int)v, dev, sizeof(dev)) < 0) {
        if (!g_field52_warned) {
          monitor_log_warn("job_gpu_assign: dropping SLURM_JOB_GPUS token %ld (no NVML map)\n", v);
          g_field52_warned = 1;
        }
        continue;
      }
    } else if (ad == JOB_GPU_AD_RSMI) {
      snprintf(dev, sizeof(dev), "%d", (int)v);
    } else if (ad == JOB_GPU_AD_ONEAPI) {
      /* Without PCI table, emit decimal V as placeholder; tests inject via grant string. */
      snprintf(dev, sizeof(dev), "%d", (int)v);
    } else {
      continue;
    }
    set_add_dev(granted, dev);
  }
  set_sort_unique(granted);
  return 0;
}

static int minor_to_dev(int minor, char *dev_out, size_t cap)
{
  int i;

  if (g_minor_fn != NULL) {
    i = g_minor_fn(minor);
    if (i < 0)
      return -1;
    snprintf(dev_out, cap, "%d", i);
    return 0;
  }
  return -1;
}

int job_gpu_assign_scan_fds(const int *pids, size_t npids, struct job_gpu_set *in_use)
{
  size_t i;
  const char *proc_root;

  if (in_use == NULL)
    return -1;
  memset(in_use, 0, sizeof(*in_use));
  if (pids == NULL || npids == 0)
    return 0;
  proc_root = job_proc_discover_proc_root();

  for (i = 0; i < npids; i++) {
    char fd_dir[512];
    DIR *d;
    struct dirent *de;
    int n;

    n = snprintf(fd_dir, sizeof(fd_dir), "%s/%d/fd", proc_root, pids[i]);
    if (n < 0 || (size_t)n >= sizeof(fd_dir))
      continue;
    d = opendir(fd_dir);
    if (d == NULL)
      continue;
    while ((de = readdir(d)) != NULL) {
      char linkpath[512];
      char target[512];
      ssize_t got;
      int minor;
      char *end;
      char dev[JOB_GPU_DEV_STR];

      if (de->d_name[0] == '.')
        continue;
      n = snprintf(linkpath, sizeof(linkpath), "%s/%s", fd_dir, de->d_name);
      if (n < 0 || (size_t)n >= sizeof(linkpath))
        continue;
      got = readlink(linkpath, target, sizeof(target) - 1);
      if (got < 0)
        continue;
      target[got] = '\0';
      if (strncmp(target, "/dev/nvidia", 11) != 0)
        continue;
      if (strcmp(target, "/dev/nvidiactl") == 0 || strcmp(target, "/dev/nvidia-uvm") == 0 ||
          strcmp(target, "/dev/nvidia-modeset") == 0 ||
          strncmp(target, "/dev/nvidia-nvswitch", 19) == 0)
        continue;
      if (strncmp(target, "/dev/nvidia", 11) == 0) {
        const char *num = target + 11;

        if (!isdigit((unsigned char)*num))
          continue;
        errno = 0;
        minor = (int)strtol(num, &end, 10);
        if (end == num || *end != '\0' || errno)
          continue;
        if (minor_to_dev(minor, dev, sizeof(dev)) == 0)
          set_add_dev(in_use, dev);
      }
    }
    closedir(d);
  }
  set_sort_unique(in_use);
  return 0;
}

void job_gpu_set_union(const struct job_gpu_set *a, const struct job_gpu_set *b,
                       struct job_gpu_set *out)
{
  size_t i;

  if (out == NULL)
    return;
  memset(out, 0, sizeof(*out));
  if (a) {
    for (i = 0; i < a->n; i++)
      set_add_dev(out, a->devs[i]);
  }
  if (b) {
    for (i = 0; i < b->n; i++)
      set_add_dev(out, b->devs[i]);
  }
  set_sort_unique(out);
}

void job_gpu_set_diff(const struct job_gpu_set *universe, const struct job_gpu_set *subtract,
                      struct job_gpu_set *out)
{
  size_t i;
  size_t j;

  if (out == NULL)
    return;
  memset(out, 0, sizeof(*out));
  if (universe == NULL)
    return;
  out->inventory_known = universe->inventory_known;
  for (i = 0; i < universe->n; i++) {
    int found = 0;

    if (subtract != NULL) {
      for (j = 0; j < subtract->n; j++) {
        if (strcmp(universe->devs[i], subtract->devs[j]) == 0) {
          found = 1;
          break;
        }
      }
    }
    if (!found)
      set_add_dev(out, universe->devs[i]);
  }
  set_sort_unique(out);
}

int job_gpu_assign_available(struct job_gpu_set *available)
{
  if (available == NULL)
    return -1;
  memset(available, 0, sizeof(*available));
  /* Without live collector enumeration in unit tests, inventory stays unknown
   * unless hooks populate via map_grant identity. Production wiring fills from
   * nvidia_gpu runtime count when DCGM is up — see job_shared_emit. */
  available->inventory_known = 0;
  return 0;
}

void job_gpu_set_format_wire(const struct job_gpu_set *set, char *out, size_t out_cap)
{
  size_t i;
  size_t used = 0;

  if (out == NULL || out_cap == 0)
    return;
  if (set == NULL || set->n == 0) {
    snprintf(out, out_cap, "-");
    return;
  }
  out[0] = '\0';
  for (i = 0; i < set->n; i++) {
    int pn = snprintf(out + used, out_cap - used, "%s%s", i ? "," : "", set->devs[i]);

    if (pn < 0 || used + (size_t)pn >= out_cap) {
      snprintf(out, out_cap, "-");
      return;
    }
    used += (size_t)pn;
  }
}

int job_gpu_set_exact_equal(const struct job_gpu_set *granted, const struct job_gpu_set *available)
{
  size_t i;

  if (granted == NULL || available == NULL)
    return 0;
  if (!available->inventory_known)
    return 0;
  if (granted->n != available->n)
    return 0;
  for (i = 0; i < granted->n; i++) {
    if (strcmp(granted->devs[i], available->devs[i]) != 0)
      return 0;
  }
  return 1;
}
