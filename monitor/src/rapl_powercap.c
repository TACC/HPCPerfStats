/*! \file rapl_powercap.c
 *  RAPL via /sys/class/powercap energy_uj (no MSR; PERF-safe).
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "rapl_powercap.h"

int rapl_powercap_parse_package_id(const char *name, unsigned *socket_out)
{
  unsigned id = 0;
  char extra = '\0';

  if (name == NULL || socket_out == NULL)
    return -1;
  if (sscanf(name, "package-%u%c", &id, &extra) != 1)
    return -1;
  *socket_out = id;
  return 0;
}

/* Parses one unsigned decimal sysfs value from fd at offset 0. 0, or -errno (-EIO if garbled). */
static int pread_ull(int fd, uint64_t *out)
{
  char buf[32];
  char *end = NULL;
  ssize_t n;
  unsigned long long v;

  n = pread(fd, buf, sizeof(buf) - 1, 0);
  if (n < 0)
    return -errno;
  if (n == 0)
    return -EIO;
  buf[n] = '\0';
  errno = 0;
  v = strtoull(buf, &end, 10);
  if (end == buf || errno != 0)
    return -EIO;
  *out = v;
  return 0;
}

/* Opens <dir>/<file> read-only; fd, or -errno. */
static int open_zone_file(const char *dir, const char *file)
{
  char path[576];
  int fd;

  if (snprintf(path, sizeof(path), "%s/%s", dir, file) >= (int)sizeof(path))
    return -ENAMETOOLONG;
  fd = open(path, O_RDONLY | O_CLOEXEC);
  return fd >= 0 ? fd : -errno;
}

static int read_name_file(const char *dir, char *name_out, size_t name_cap)
{
  int fd = open_zone_file(dir, "name");
  ssize_t n;

  if (fd < 0)
    return -1;
  n = pread(fd, name_out, name_cap - 1, 0);
  close(fd);
  if (n <= 0)
    return -1;
  name_out[n] = '\0';
  while (n > 0 && (name_out[n - 1] == '\n' || name_out[n - 1] == '\r'))
    name_out[--n] = '\0';
  return n > 0 ? 0 : -1;
}

static int is_rapl_zone_dir(const char *d_name)
{
  return strncmp(d_name, "intel-rapl:", 11) == 0 || strncmp(d_name, "amd-rapl:", 9) == 0;
}

/* Top package zones: intel-rapl:N (one index). Children: intel-rapl:N:M. */
static int zone_is_package_top(const char *d_name)
{
  const char *colon;

  if (!is_rapl_zone_dir(d_name))
    return 0;
  colon = strchr(d_name, ':');
  return strchr(colon + 1, ':') == NULL;
}

static int name_in_list(const char *name, const char *const *names)
{
  for (; *names != NULL; names++)
    if (strcmp(name, *names) == 0)
      return 1;
  return 0;
}

/* Directory entry (not path) of socket_id's package zone into pkg_base; 0 or -ENOENT. */
static int find_package_entry(const char *powercap_root, unsigned socket_id, char *pkg_base,
                              size_t cap)
{
  DIR *dir = opendir(powercap_root);
  struct dirent *ent;
  int rc = -ENOENT;

  if (dir == NULL)
    return -ENOENT;
  while ((ent = readdir(dir)) != NULL) {
    char zone[512];
    char name[128];
    unsigned sock = 0;

    if (!zone_is_package_top(ent->d_name))
      continue;
    snprintf(zone, sizeof(zone), "%s/%s", powercap_root, ent->d_name);
    if (read_name_file(zone, name, sizeof(name)) < 0 ||
        rapl_powercap_parse_package_id(name, &sock) < 0 || sock != socket_id)
      continue;
    snprintf(pkg_base, cap, "%s", ent->d_name);
    rc = 0;
    break;
  }
  closedir(dir);
  return rc;
}

int rapl_powercap_find_zone(const char *powercap_root, unsigned socket_id,
                            const char *const *child_names, char *out, size_t cap)
{
  DIR *dir;
  struct dirent *ent;
  char pkg_base[256];
  size_t base_len;
  int rc = -ENOENT;

  if (powercap_root == NULL || out == NULL || cap == 0 ||
      find_package_entry(powercap_root, socket_id, pkg_base, sizeof(pkg_base)) < 0)
    return -ENOENT;
  if (child_names == NULL) {
    snprintf(out, cap, "%s/%s", powercap_root, pkg_base);
    return 0;
  }
  /* Kernel layout: children are peers under the powercap root (intel-rapl:0:0). */
  base_len = strlen(pkg_base);
  dir = opendir(powercap_root);
  if (dir == NULL)
    return -ENOENT;
  while ((ent = readdir(dir)) != NULL) {
    char child[576];
    char name[128];

    if (strncmp(ent->d_name, pkg_base, base_len) != 0 || ent->d_name[base_len] != ':')
      continue;
    snprintf(child, sizeof(child), "%s/%s", powercap_root, ent->d_name);
    if (read_name_file(child, name, sizeof(name)) < 0 || !name_in_list(name, child_names))
      continue;
    snprintf(out, cap, "%s", child);
    rc = 0;
    break;
  }
  closedir(dir);
  return rc;
}

int rapl_powercap_wrap_delta(uint64_t prev_uj, uint64_t cur_uj, uint64_t max_uj, uint64_t *delta_uj)
{
  if (cur_uj >= prev_uj) {
    *delta_uj = cur_uj - prev_uj;
    return 0;
  }
  if (max_uj < prev_uj)
    return -ERANGE;
  *delta_uj = max_uj - prev_uj + cur_uj;
  return 0;
}

int rapl_powercap_counter_open(struct rapl_powercap_counter *c, const char *zone_dir)
{
  int max_fd;
  int rc;

  c->fd = -1;
  c->max_uj = 0;
  c->prev_uj = 0;
  rc = open_zone_file(zone_dir, "energy_uj");
  if (rc < 0)
    return rc;
  c->fd = rc;
  rc = pread_ull(c->fd, &c->prev_uj);
  if (rc < 0) {
    rapl_powercap_counter_close(c);
    return rc;
  }
  max_fd = open_zone_file(zone_dir, "max_energy_range_uj");
  if (max_fd >= 0) {
    if (pread_ull(max_fd, &c->max_uj) < 0)
      c->max_uj = 0;
    close(max_fd);
  }
  return 0;
}

int rapl_powercap_counter_read(struct rapl_powercap_counter *c, uint64_t *delta_uj)
{
  uint64_t cur = 0;
  int rc;

  *delta_uj = 0;
  rc = pread_ull(c->fd, &cur);
  if (rc < 0)
    return rc;
  rc = rapl_powercap_wrap_delta(c->prev_uj, cur, c->max_uj, delta_uj);
  c->prev_uj = cur;
  return rc;
}

void rapl_powercap_counter_close(struct rapl_powercap_counter *c)
{
  if (c->fd >= 0)
    close(c->fd);
  c->fd = -1;
}
