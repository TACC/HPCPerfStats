/* intel_gpu_xpumcli — fork/exec xpumcli for intel_gpu without lasting libxpum/GEM maps. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "intel_gpu_xpumcli.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "monitor_log.h"
#include "stats.h"
#include "trace.h"

#ifdef INTEL_GPU_TEST_BUILD
static intel_gpu_xpumcli_capture_fn g_test_capture;
void intel_gpu_xpumcli_test_set_capture(intel_gpu_xpumcli_capture_fn fn)
{
  g_test_capture = fn;
}
void intel_gpu_xpumcli_test_reset(void)
{
  g_test_capture = NULL;
}
#endif

static int intel_gpu_xpumcli_read_fd(int fd, char *out, size_t out_cap, int timeout_ms)
{
  size_t used = 0;
  long long deadline_ms;
  struct timespec ts;

  if (out == NULL || out_cap < 2)
    return -1;
  out[0] = '\0';
  if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
    return -1;
  deadline_ms = (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL + timeout_ms;

  while (used + 1 < out_cap) {
    struct pollfd pfd;
    int pr;
    long long now_ms;
    int wait_ms;
    ssize_t nr;

    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
      return -1;
    now_ms = (long long)ts.tv_sec * 1000LL + ts.tv_nsec / 1000000LL;
    wait_ms = (int)(deadline_ms - now_ms);
    if (wait_ms <= 0)
      return -1;
    pfd.fd = fd;
    pfd.events = POLLIN;
    pfd.revents = 0;
    pr = poll(&pfd, 1, wait_ms);
    if (pr < 0) {
      if (errno == EINTR)
        continue;
      return -1;
    }
    if (pr == 0)
      return -1;
    nr = read(fd, out + used, out_cap - 1 - used);
    if (nr < 0) {
      if (errno == EINTR)
        continue;
      return -1;
    }
    if (nr == 0)
      break;
    used += (size_t)nr;
  }
  out[used] = '\0';
  return 0;
}

static int intel_gpu_xpumcli_capture(char *const argv[], char *out, size_t out_cap)
{
  int pipefd[2];
  pid_t pid;
  int status = -1;

  if (argv == NULL || argv[0] == NULL || out == NULL)
    return -1;
#ifdef INTEL_GPU_TEST_BUILD
  if (g_test_capture != NULL)
    return g_test_capture(argv, out, out_cap);
#endif
  if (pipe(pipefd) != 0)
    return -1;
  pid = fork();
  if (pid < 0) {
    close(pipefd[0]);
    close(pipefd[1]);
    return -1;
  }
  if (pid == 0) {
    close(pipefd[0]);
    if (dup2(pipefd[1], STDOUT_FILENO) < 0)
      _exit(127);
    close(pipefd[1]);
    {
      int nullfd = open("/dev/null", O_WRONLY);
      if (nullfd >= 0) {
        (void)dup2(nullfd, STDERR_FILENO);
        close(nullfd);
      }
    }
    execvp(argv[0], argv);
    _exit(127);
  }
  close(pipefd[1]);
  if (intel_gpu_xpumcli_read_fd(pipefd[0], out, out_cap, INTEL_GPU_XPUMCLI_CAPTURE_TIMEOUT_MS) !=
      0) {
    kill(pid, SIGKILL);
    (void)waitpid(pid, &status, 0);
    close(pipefd[0]);
    return -1;
  }
  close(pipefd[0]);
  if (waitpid(pid, &status, 0) < 0)
    return -1;
  if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
    return -1;
  return 0;
}

static int intel_gpu_xpumcli_field_is_na(const char *s)
{
  while (s != NULL && *s && isspace((unsigned char)*s))
    s++;
  if (s == NULL || *s == '\0')
    return 1;
  if (strncasecmp(s, "N/A", 3) == 0)
    return 1;
  return 0;
}

static int intel_gpu_xpumcli_parse_double(const char *s, double *out)
{
  char *end = NULL;
  double v;

  if (out == NULL || intel_gpu_xpumcli_field_is_na(s))
    return -1;
  errno = 0;
  v = strtod(s, &end);
  if (end == s || errno == ERANGE)
    return -1;
  *out = v;
  return 0;
}

int intel_gpu_xpumcli_parse_discovery(const char *text, int *device_ids, int max_devices,
                                      int *out_count)
{
  const char *p;
  int count = 0;

  if (text == NULL || device_ids == NULL || out_count == NULL || max_devices <= 0)
    return -1;
  *out_count = 0;
  p = text;
  while (*p && count < max_devices) {
    const char *nl = strchr(p, '\n');
    char line[512];
    size_t len;
    int id;
    const char *bar;

    len = nl != NULL ? (size_t)(nl - p) : strlen(p);
    if (len >= sizeof(line))
      len = sizeof(line) - 1;
    memcpy(line, p, len);
    line[len] = '\0';
    /* Table rows: "| 0         | Device Name: ..." */
    bar = strchr(line, '|');
    if (bar != NULL) {
      bar++;
      while (*bar && isspace((unsigned char)*bar))
        bar++;
      if (isdigit((unsigned char)*bar)) {
        id = atoi(bar);
        if (id >= 0 && strstr(line, "Device Name") != NULL) {
          int i;
          int dup = 0;
          for (i = 0; i < count; i++) {
            if (device_ids[i] == id) {
              dup = 1;
              break;
            }
          }
          if (!dup)
            device_ids[count++] = id;
        }
      }
    }
    if (nl == NULL)
      break;
    p = nl + 1;
  }
  *out_count = count;
  return count > 0 ? 0 : -1;
}

static int intel_gpu_xpumcli_header_col(char **fields, int nfields, const char *needle)
{
  int i;
  for (i = 0; i < nfields; i++) {
    if (fields[i] != NULL && strcasestr(fields[i], needle) != NULL)
      return i;
  }
  return -1;
}

int intel_gpu_xpumcli_parse_dump_csv(const char *text, struct intel_gpu_xpumcli_sample *samples,
                                     int max_samples)
{
  char *copy;
  char *save = NULL;
  char *line;
  char *header = NULL;
  char *hfields[64];
  int nh = 0;
  int col_dev = -1;
  int col_util = -1;
  int col_power = -1;
  int col_temp_core = -1;
  int col_temp_mem = -1;
  int col_mem_util = -1;
  int col_mem_used = -1;
  int col_freq = -1;
  int col_eu = -1;
  int col_bw = -1;
  int col_throttle = -1;
  int nsamples = 0;

  if (text == NULL || samples == NULL || max_samples <= 0)
    return 0;
  copy = strdup(text);
  if (copy == NULL)
    return 0;

  for (line = strtok_r(copy, "\n", &save); line != NULL; line = strtok_r(NULL, "\n", &save)) {
    while (*line && isspace((unsigned char)*line))
      line++;
    if (*line == '\0')
      continue;
    if (header == NULL) {
      if (strcasestr(line, "DeviceId") == NULL && strcasestr(line, "Device Id") == NULL)
        continue;
      header = line;
      nh = 0;
      {
        char *hcopy = strdup(header);
        char *tok;
        char *hsave = NULL;
        if (hcopy == NULL)
          break;
        for (tok = strtok_r(hcopy, ",", &hsave); tok != NULL && nh < 64;
             tok = strtok_r(NULL, ",", &hsave)) {
          while (*tok && isspace((unsigned char)*tok))
            tok++;
          hfields[nh++] = tok;
        }
        col_dev = intel_gpu_xpumcli_header_col(hfields, nh, "DeviceId");
        if (col_dev < 0)
          col_dev = intel_gpu_xpumcli_header_col(hfields, nh, "Device Id");
        col_util = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Utilization");
        if (col_util < 0)
          col_util = intel_gpu_xpumcli_header_col(hfields, nh, "utilization of all GPU");
        col_power = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Power");
        col_temp_core = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Core Temperature");
        col_temp_mem = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Memory Temperature");
        col_mem_util = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Memory Utilization");
        col_mem_used = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Memory Used");
        col_freq = intel_gpu_xpumcli_header_col(hfields, nh, "GPU Frequency");
        col_eu = intel_gpu_xpumcli_header_col(hfields, nh, "EU Array Active");
        col_bw = intel_gpu_xpumcli_header_col(hfields, nh, "Memory Bandwidth");
        col_throttle = intel_gpu_xpumcli_header_col(hfields, nh, "Throttle");
        free(hcopy);
      }
      if (col_dev < 0)
        break;
      continue;
    }
    /* data row */
    {
      char *row = strdup(line);
      char *fields[64];
      int nf = 0;
      char *tok;
      char *rsave = NULL;
      struct intel_gpu_xpumcli_sample *s;
      double v;

      if (row == NULL)
        continue;
      for (tok = strtok_r(row, ",", &rsave); tok != NULL && nf < 64;
           tok = strtok_r(NULL, ",", &rsave)) {
        while (*tok && isspace((unsigned char)*tok))
          tok++;
        fields[nf++] = tok;
      }
      if (nf <= col_dev) {
        free(row);
        continue;
      }
      if (nsamples >= max_samples) {
        free(row);
        break;
      }
      s = &samples[nsamples];
      memset(s, 0, sizeof(*s));
      s->device_id = atoi(fields[col_dev]);
      if (col_util >= 0 && col_util < nf &&
          intel_gpu_xpumcli_parse_double(fields[col_util], &v) == 0) {
        s->has_gpu_util = 1;
        s->gpu_util = v;
      }
      if (col_power >= 0 && col_power < nf &&
          intel_gpu_xpumcli_parse_double(fields[col_power], &v) == 0) {
        s->has_power = 1;
        s->power_w = v;
      }
      if (col_temp_core >= 0 && col_temp_core < nf &&
          intel_gpu_xpumcli_parse_double(fields[col_temp_core], &v) == 0) {
        s->has_temp = 1;
        s->temp_c = v;
      } else if (col_temp_mem >= 0 && col_temp_mem < nf &&
                 intel_gpu_xpumcli_parse_double(fields[col_temp_mem], &v) == 0) {
        s->has_temp = 1;
        s->temp_c = v;
      }
      if (col_mem_util >= 0 && col_mem_util < nf &&
          intel_gpu_xpumcli_parse_double(fields[col_mem_util], &v) == 0) {
        s->has_mem_util = 1;
        s->mem_util = v;
      }
      if (col_mem_used >= 0 && col_mem_used < nf &&
          intel_gpu_xpumcli_parse_double(fields[col_mem_used], &v) == 0) {
        s->has_mem_used_mb = 1;
        s->mem_used_mb = v; /* dump already MiB */
      }
      if (col_freq >= 0 && col_freq < nf &&
          intel_gpu_xpumcli_parse_double(fields[col_freq], &v) == 0) {
        s->has_freq = 1;
        s->freq_mhz = v;
      }
      if (col_eu >= 0 && col_eu < nf && intel_gpu_xpumcli_parse_double(fields[col_eu], &v) == 0) {
        s->has_eu_active = 1;
        s->eu_active = v;
      }
      if (col_bw >= 0 && col_bw < nf && intel_gpu_xpumcli_parse_double(fields[col_bw], &v) == 0) {
        s->has_mem_bw = 1;
        s->mem_bw = v;
      }
      if (col_throttle >= 0 && col_throttle < nf &&
          !intel_gpu_xpumcli_field_is_na(fields[col_throttle])) {
        /* textual throttle; store 1 if not "Not Throttled" */
        if (strcasestr(fields[col_throttle], "Not Throttled") == NULL) {
          s->has_throttle = 1;
          s->throttle_flags = 1;
        } else {
          s->has_throttle = 1;
          s->throttle_flags = 0;
        }
      }
      nsamples++;
      free(row);
    }
  }
  free(copy);
  return nsamples;
}

void intel_gpu_xpumcli_publish_sample(struct stats *stats, const struct intel_gpu_xpumcli_sample *s,
                                      int gpu_count)
{
  unsigned long long mem_total_mb = 0;

  if (stats == NULL || s == NULL)
    return;
  if (s->has_gpu_util)
    stats_set(stats, "gpu_util", (unsigned long long)(s->gpu_util + 0.5));
  if (s->has_mem_util)
    stats_set(stats, "gpu_mem_util", (unsigned long long)(s->mem_util + 0.5));
  if (s->has_mem_used_mb)
    stats_set(stats, "gpu_mem_used_mb", (unsigned long long)(s->mem_used_mb + 0.5));
  if (s->has_mem_util && s->has_mem_used_mb && s->mem_util > 0.0)
    mem_total_mb = (unsigned long long)((s->mem_used_mb / (s->mem_util / 100.0)) + 0.5);
  stats_set(stats, "gpu_mem_total_mb", mem_total_mb);
  if (s->has_power)
    stats_set(stats, "power_usage", (unsigned long long)(s->power_w + 0.5));
  if (s->has_temp)
    stats_set(stats, "temperature", (unsigned long long)(s->temp_c + 0.5));
  if (s->has_freq)
    stats_set(stats, "gpu_sm_clock", (unsigned long long)(s->freq_mhz + 0.5));
  if (s->has_eu_active)
    stats_set(stats, "sm_active", (unsigned long long)(s->eu_active + 0.5));
  if (s->has_mem_bw)
    stats_set(stats, "gpu_dram_active", (unsigned long long)(s->mem_bw + 0.5));
  stats_set(stats, "gpu_pcie_rx_bytes", 0);
  stats_set(stats, "gpu_pcie_tx_bytes", 0);
  stats_set(stats, "gpu_xe_link_rx_bytes", 0);
  stats_set(stats, "gpu_xe_link_tx_bytes", 0);
  if (s->has_throttle)
    stats_set(stats, "clocks_event_reasons", s->throttle_flags);
  else
    stats_set(stats, "clocks_event_reasons", 0);
  stats_set(stats, "gpu_count", (unsigned long long)gpu_count);
}

int intel_gpu_xpumcli_collect(struct stats_type *type)
{
  char disc_buf[65536];
  char dump_buf[262144];
  char *argv_disc[4];
  char *argv_dump[16];
  char dev_csv[128];
  int device_ids[INTEL_GPU_XPUMCLI_MAX_DEVICES];
  int ndev = 0;
  struct intel_gpu_xpumcli_sample samples[INTEL_GPU_XPUMCLI_MAX_DEVICES];
  int nsamples;
  int i;
  int ok_any = 0;
  size_t pos = 0;
  int ai;

  if (type == NULL || !type->st_enabled)
    return -1;

  argv_disc[0] = "xpumcli";
  argv_disc[1] = "discovery";
  argv_disc[2] = NULL;
  if (intel_gpu_xpumcli_capture(argv_disc, disc_buf, sizeof(disc_buf)) != 0) {
    TRACE("intel_gpu: xpumcli discovery failed\n");
    return -1;
  }
  if (intel_gpu_xpumcli_parse_discovery(disc_buf, device_ids, INTEL_GPU_XPUMCLI_MAX_DEVICES,
                                        &ndev) != 0 ||
      ndev <= 0) {
    TRACE("intel_gpu: xpumcli discovery parsed no devices\n");
    return -1;
  }

  pos = 0;
  for (i = 0; i < ndev; i++) {
    int n =
        snprintf(dev_csv + pos, sizeof(dev_csv) - pos, "%s%d", i == 0 ? "" : ",", device_ids[i]);
    if (n < 0 || (size_t)n >= sizeof(dev_csv) - pos)
      return -1;
    pos += (size_t)n;
  }

  ai = 0;
  argv_dump[ai++] = "xpumcli";
  argv_dump[ai++] = "dump";
  argv_dump[ai++] = "-d";
  argv_dump[ai++] = dev_csv;
  argv_dump[ai++] = "-m";
  argv_dump[ai++] = INTEL_GPU_XPUMCLI_DUMP_METRICS;
  argv_dump[ai++] = "-i";
  argv_dump[ai++] = "1";
  argv_dump[ai++] = "-n";
  argv_dump[ai++] = "1";
  argv_dump[ai] = NULL;

  if (intel_gpu_xpumcli_capture(argv_dump, dump_buf, sizeof(dump_buf)) != 0) {
    TRACE("intel_gpu: xpumcli dump failed\n");
    return -1;
  }
  nsamples = intel_gpu_xpumcli_parse_dump_csv(dump_buf, samples, INTEL_GPU_XPUMCLI_MAX_DEVICES);
  if (nsamples <= 0)
    return -1;

  for (i = 0; i < nsamples; i++) {
    char dev[16];
    struct stats *stats;

    snprintf(dev, sizeof(dev), "%d", samples[i].device_id);
    stats = get_current_stats(type, dev);
    if (stats == NULL)
      continue;
    intel_gpu_xpumcli_publish_sample(stats, &samples[i], ndev);
    ok_any = 1;
  }
  return ok_any ? 0 : -1;
}
