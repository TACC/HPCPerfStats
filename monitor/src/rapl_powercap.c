/*! \file rapl_powercap.c
 *  RAPL via /sys/class/powercap energy_uj (no MSR; PERF-safe).
 */

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rapl_powercap.h"

unsigned long long rapl_powercap_uj_to_mj(unsigned long long uj)
{
  return (uj + 500ULL) / 1000ULL;
}

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

const char *rapl_powercap_schema_key_from_name(const char *name, int amd_path)
{
  if (name == NULL || name[0] == '\0')
    return NULL;
  if (strcmp(name, "core") == 0 || strcmp(name, "cores") == 0)
    return amd_path ? "core_energy" : "pp0_energy";
  if (strcmp(name, "dram") == 0 || strcmp(name, "ram") == 0)
    return "dram_energy";
  return NULL;
}

static int read_ull_file(const char *path, unsigned long long *out)
{
  FILE *fp;
  char buf[64];
  char *end = NULL;
  unsigned long long v;

  if (path == NULL || out == NULL)
    return -1;
  fp = fopen(path, "r");
  if (fp == NULL)
    return -1;
  if (fgets(buf, sizeof(buf), fp) == NULL) {
    fclose(fp);
    return -1;
  }
  fclose(fp);
  errno = 0;
  v = strtoull(buf, &end, 10);
  if (end == buf || errno != 0)
    return -1;
  *out = v;
  return 0;
}

static int read_name_file(const char *dir, char *name_out, size_t name_cap)
{
  char path[512];
  FILE *fp;
  size_t n;

  if (dir == NULL || name_out == NULL || name_cap == 0)
    return -1;
  snprintf(path, sizeof(path), "%s/name", dir);
  fp = fopen(path, "r");
  if (fp == NULL)
    return -1;
  if (fgets(name_out, (int)name_cap, fp) == NULL) {
    fclose(fp);
    return -1;
  }
  fclose(fp);
  n = strlen(name_out);
  while (n > 0 && (name_out[n - 1] == '\n' || name_out[n - 1] == '\r')) {
    name_out[n - 1] = '\0';
    n--;
  }
  return name_out[0] != '\0' ? 0 : -1;
}

static int is_rapl_zone_dir(const char *d_name)
{
  return d_name != NULL &&
         (strncmp(d_name, "intel-rapl:", 11) == 0 || strncmp(d_name, "amd-rapl:", 9) == 0);
}

/* Top package zones: intel-rapl:N (one index). Children: intel-rapl:N:M. */
static int zone_is_package_top(const char *d_name)
{
  const char *colon;

  if (!is_rapl_zone_dir(d_name))
    return 0;
  colon = strchr(d_name, ':');
  if (colon == NULL)
    return 0;
  return strchr(colon + 1, ':') == NULL;
}

int rapl_powercap_available_under(const char *powercap_root)
{
  DIR *dir;
  struct dirent *ent;
  int found = 0;

  if (powercap_root == NULL || powercap_root[0] == '\0')
    return 0;
  dir = opendir(powercap_root);
  if (dir == NULL)
    return 0;
  while ((ent = readdir(dir)) != NULL) {
    char zone[512];
    char name[128];
    unsigned sock = 0;

    if (!zone_is_package_top(ent->d_name))
      continue;
    snprintf(zone, sizeof(zone), "%s/%s", powercap_root, ent->d_name);
    if (read_name_file(zone, name, sizeof(name)) < 0)
      continue;
    if (rapl_powercap_parse_package_id(name, &sock) == 0) {
      found = 1;
      break;
    }
  }
  closedir(dir);
  return found;
}

int rapl_powercap_available(void)
{
  return rapl_powercap_available_under(RAPL_POWERCAP_DEFAULT_ROOT);
}

static int read_energy_mj(const char *zone_dir, unsigned long long *mj_out)
{
  char path[576];
  unsigned long long uj = 0;

  if (zone_dir == NULL || mj_out == NULL)
    return -1;
  snprintf(path, sizeof(path), "%s/energy_uj", zone_dir);
  if (read_ull_file(path, &uj) < 0)
    return -1;
  *mj_out = rapl_powercap_uj_to_mj(uj);
  return 0;
}

static void apply_domain_mj(const char *key, unsigned long long mj, unsigned long long *pkg_mj,
                            unsigned long long *core_mj, unsigned long long *dram_mj, int *has_pkg,
                            int *has_core, int *has_dram, unsigned long long *pp1_mj, int *has_pp1)
{
  if (key == NULL)
    return;
  if (strcmp(key, "pkg_energy") == 0) {
    *pkg_mj = mj;
    *has_pkg = 1;
  } else if (strcmp(key, "pp0_energy") == 0 || strcmp(key, "core_energy") == 0) {
    *core_mj = mj;
    *has_core = 1;
  } else if (strcmp(key, "pp1_energy") == 0) {
    if (pp1_mj != NULL && has_pp1 != NULL) {
      *pp1_mj = mj;
      *has_pp1 = 1;
    }
  } else if (strcmp(key, "dram_energy") == 0) {
    *dram_mj = mj;
    *has_dram = 1;
  }
}

static void collect_child_zone(const char *child_dir, int amd_path, unsigned long long *pkg_mj,
                               unsigned long long *core_mj, unsigned long long *dram_mj,
                               int *has_pkg, int *has_core, int *has_dram,
                               unsigned long long *pp1_mj, int *has_pp1)
{
  char cname[128];
  const char *key;
  unsigned long long mj = 0;

  if (read_name_file(child_dir, cname, sizeof(cname)) < 0)
    return;
  key = rapl_powercap_schema_key_from_name(cname, amd_path);
  if (key == NULL)
    return;
  if (read_energy_mj(child_dir, &mj) == 0)
    apply_domain_mj(key, mj, pkg_mj, core_mj, dram_mj, has_pkg, has_core, has_dram, pp1_mj,
                    has_pp1);
}

int rapl_powercap_collect_socket_mj_under(const char *powercap_root, unsigned socket_id,
                                          unsigned long long *pkg_mj, unsigned long long *core_mj,
                                          unsigned long long *dram_mj, int *has_pkg, int *has_core,
                                          int *has_dram, unsigned long long *pp1_mj, int *has_pp1,
                                          int amd_path)
{
  DIR *dir;
  struct dirent *ent;
  char pkg_zone[512];
  char pkg_base[256];
  char pkg_name[128];
  unsigned long long mj = 0;
  int found_pkg_dir = 0;
  size_t pkg_base_len;

  if (pkg_mj == NULL || core_mj == NULL || dram_mj == NULL || has_pkg == NULL || has_core == NULL ||
      has_dram == NULL || powercap_root == NULL)
    return -1;
  *pkg_mj = *core_mj = *dram_mj = 0;
  *has_pkg = *has_core = *has_dram = 0;
  if (pp1_mj != NULL && has_pp1 != NULL) {
    *pp1_mj = 0;
    *has_pp1 = 0;
  }

  dir = opendir(powercap_root);
  if (dir == NULL)
    return -1;

  pkg_zone[0] = '\0';
  pkg_base[0] = '\0';
  while ((ent = readdir(dir)) != NULL) {
    unsigned sock = 0;

    if (!zone_is_package_top(ent->d_name))
      continue;
    snprintf(pkg_zone, sizeof(pkg_zone), "%s/%s", powercap_root, ent->d_name);
    if (read_name_file(pkg_zone, pkg_name, sizeof(pkg_name)) < 0)
      continue;
    if (rapl_powercap_parse_package_id(pkg_name, &sock) < 0)
      continue;
    if (sock != socket_id)
      continue;
    snprintf(pkg_base, sizeof(pkg_base), "%s", ent->d_name);
    found_pkg_dir = 1;
    break;
  }
  closedir(dir);

  if (!found_pkg_dir)
    return -1;

  if (read_energy_mj(pkg_zone, &mj) == 0)
    apply_domain_mj("pkg_energy", mj, pkg_mj, core_mj, dram_mj, has_pkg, has_core, has_dram, pp1_mj,
                    has_pp1);

  /* Kernel layout: children are peers under powercap root (intel-rapl:0:0). */
  pkg_base_len = strlen(pkg_base);
  dir = opendir(powercap_root);
  if (dir != NULL) {
    while ((ent = readdir(dir)) != NULL) {
      char child[576];

      if (!is_rapl_zone_dir(ent->d_name) || zone_is_package_top(ent->d_name))
        continue;
      if (strncmp(ent->d_name, pkg_base, pkg_base_len) != 0 || ent->d_name[pkg_base_len] != ':')
        continue;
      snprintf(child, sizeof(child), "%s/%s", powercap_root, ent->d_name);
      collect_child_zone(child, amd_path, pkg_mj, core_mj, dram_mj, has_pkg, has_core, has_dram,
                         pp1_mj, has_pp1);
    }
    closedir(dir);
  }

  if (*has_pkg || *has_core || *has_dram || (has_pp1 != NULL && *has_pp1))
    return 0;
  return -1;
}

int rapl_powercap_collect_socket_mj(unsigned socket_id, unsigned long long *pkg_mj,
                                    unsigned long long *core_mj, unsigned long long *dram_mj,
                                    int *has_pkg, int *has_core, int *has_dram,
                                    unsigned long long *pp1_mj, int *has_pp1, int amd_path)
{
  return rapl_powercap_collect_socket_mj_under(RAPL_POWERCAP_DEFAULT_ROOT, socket_id, pkg_mj,
                                               core_mj, dram_mj, has_pkg, has_core, has_dram,
                                               pp1_mj, has_pp1, amd_path);
}
