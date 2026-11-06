/* ACPI power meter index for host_power_socket (see arm_power_hwmon.h). Portable sysfs code:
 * built on every arch; x86 nodes simply have no "<Name> Power Socket <N>" meters. */
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "arm_power_hwmon.h"
#include "monitor_log.h"

#define ARM_POWER_PATH_MAX 512
#define ARM_POWER_LABEL_MAX 128
#define ARM_POWER_SOCKET_TOKEN " Power Socket "

static const char *const g_arm_power_names[ARM_POWER_KIND_COUNT] = {"CPU", "Grace", "SysIO",
                                                                    "Module"};

int arm_power_hwmon_parse_label(const char *label, int *kind, int *sock)
{
  const char *tok;
  char *end = NULL;
  size_t name_len;
  long n;
  int k;

  if (label == NULL || kind == NULL || sock == NULL)
    return -1;
  tok = strstr(label, ARM_POWER_SOCKET_TOKEN);
  if (tok == NULL || tok == label)
    return -1;
  name_len = (size_t)(tok - label);
  tok += strlen(ARM_POWER_SOCKET_TOKEN);
  if (*tok < '0' || *tok > '9')
    return -1;
  n = strtol(tok, &end, 10);
  while (*end == '\n' || *end == ' ' || *end == '\t' || *end == '\r')
    end++;
  if (*end != '\0' || n < 0 || n >= ARM_POWER_HWMON_MAX_SOCKETS)
    return -1;
  *sock = (int)n;
  for (k = 0; k < ARM_POWER_KIND_COUNT; k++) {
    if (strlen(g_arm_power_names[k]) == name_len &&
        strncmp(label, g_arm_power_names[k], name_len) == 0) {
      *kind = k;
      return 0;
    }
  }
  return 1;
}

/* Read the meter label from dir/power1_oem_info (ACPI _PMI OEM string) or dir/power1_label. */
static int arm_power_read_label(const char *dir, char *out, size_t out_sz)
{
  static const char *const attrs[] = {"power1_oem_info", "power1_label"};
  size_t a;

  for (a = 0; a < sizeof(attrs) / sizeof(attrs[0]); a++) {
    char path[ARM_POWER_PATH_MAX];
    ssize_t n;
    int fd;

    if (snprintf(path, sizeof(path), "%s/%s", dir, attrs[a]) >= (int)sizeof(path))
      continue;
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
      continue;
    n = read(fd, out, out_sz - 1);
    close(fd);
    if (n > 0) {
      out[n] = '\0';
      return 0;
    }
  }
  return -1;
}

static void arm_power_add_meter(struct arm_power_hwmon *h, const char *dir, int kind, int sock)
{
  char path[ARM_POWER_PATH_MAX];
  int fd;

  if (arm_power_hwmon_find(h, kind, sock) >= 0 || h->n >= ARM_POWER_HWMON_MAX_METERS)
    return;
  if (snprintf(path, sizeof(path), "%s/power1_average", dir) >= (int)sizeof(path))
    return;
  fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) {
    monitor_log_warn("host_power_socket: %s unreadable; %s Power Socket %d publishes 0\n", path,
                     g_arm_power_names[kind], sock);
    return;
  }
  h->m[h->n].kind = kind;
  h->m[h->n].sock = sock;
  h->m[h->n].fd = fd;
  h->m[h->n].read_warned = 0;
  h->n++;
  if (sock + 1 > h->nsock)
    h->nsock = sock + 1;
}

/* One hwmonN entry: attributes directly in hwmonN (EL10) or in hwmonN/device (EL9). */
static void arm_power_scan_hwmon(struct arm_power_hwmon *h, const char *hwmon_dir)
{
  static const char *const subdirs[] = {"", "/device"};
  size_t d;

  for (d = 0; d < sizeof(subdirs) / sizeof(subdirs[0]); d++) {
    char dir[ARM_POWER_PATH_MAX];
    char label[ARM_POWER_LABEL_MAX];
    int kind = -1, sock = -1, rc;

    if (snprintf(dir, sizeof(dir), "%s%s", hwmon_dir, subdirs[d]) >= (int)sizeof(dir))
      continue;
    if (arm_power_read_label(dir, label, sizeof(label)) != 0)
      continue;
    rc = arm_power_hwmon_parse_label(label, &kind, &sock);
    if (rc == 0)
      arm_power_add_meter(h, dir, kind, sock);
    else if (rc == 1)
      monitor_log_warn("host_power_socket: unknown power meter label '%.*s' in %s; ignored\n",
                       (int)strcspn(label, "\n"), label, dir);
    return; /* a labelled directory was found; do not also scan device/ */
  }
}

int arm_power_hwmon_open(struct arm_power_hwmon *h, const char *sysfs_root)
{
  char class_dir[ARM_POWER_PATH_MAX];
  DIR *dp;
  struct dirent *de;

  if (h == NULL)
    return 0;
  memset(h, 0, sizeof(*h));
  if (sysfs_root == NULL || snprintf(class_dir, sizeof(class_dir), "%s/class/hwmon", sysfs_root) >=
                                (int)sizeof(class_dir))
    return 0;
  dp = opendir(class_dir);
  if (dp == NULL)
    return 0;
  while ((de = readdir(dp)) != NULL) {
    char hwmon_dir[ARM_POWER_PATH_MAX];

    if (strncmp(de->d_name, "hwmon", 5) != 0)
      continue;
    if (snprintf(hwmon_dir, sizeof(hwmon_dir), "%s/%s", class_dir, de->d_name) >=
        (int)sizeof(hwmon_dir))
      continue;
    arm_power_scan_hwmon(h, hwmon_dir);
  }
  closedir(dp);
  return h->n;
}

int arm_power_hwmon_find(const struct arm_power_hwmon *h, int kind, int sock)
{
  int i;

  if (h == NULL)
    return -1;
  for (i = 0; i < h->n; i++) {
    if (h->m[i].kind == kind && h->m[i].sock == sock)
      return i;
  }
  return -1;
}

double arm_power_hwmon_read_w(struct arm_power_hwmon *h, int slot)
{
  char buf[32];
  ssize_t n;
  long long uw = -1;

  if (h == NULL || slot < 0 || slot >= h->n)
    return 0.0;
  n = pread(h->m[slot].fd, buf, sizeof(buf) - 1, 0);
  if (n > 0) {
    buf[n] = '\0';
    if (sscanf(buf, "%lld", &uw) != 1)
      uw = -1;
  }
  if (uw < 0) {
    if (!h->m[slot].read_warned) {
      monitor_log_warn("host_power_socket: %s Power Socket %d read failed; publishing 0\n",
                       g_arm_power_names[h->m[slot].kind], h->m[slot].sock);
      h->m[slot].read_warned = 1;
    }
    return 0.0;
  }
  return (double)uw / 1e6;
}

void arm_power_hwmon_close(struct arm_power_hwmon *h)
{
  int i;

  if (h == NULL)
    return;
  for (i = 0; i < h->n; i++) {
    if (h->m[i].fd >= 0)
      close(h->m[i].fd);
  }
  memset(h, 0, sizeof(*h));
}
