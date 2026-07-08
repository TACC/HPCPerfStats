/*! \file host_edac_mem_topology.c
 *  EDAC dimm_mem_type scan shared by roofline peak detect and SPR IMC eventset.
 */

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "host_edac_mem_topology.h"

#define EDAC_MC_DEFAULT "/sys/devices/system/edac/mc"

static const char *host_edac_mc_root(void)
{
  const char *env = getenv("HPCPERFSTATS_EDAC_MC_ROOT");

  return (env != NULL && env[0] != '\0') ? env : EDAC_MC_DEFAULT;
}

static int read_first_line(const char *path, char *buf, size_t cap)
{
  FILE *f = fopen(path, "re");

  if (f == NULL)
    return -1;
  if (fgets(buf, (int) cap, f) == NULL) {
    fclose(f);
    return -1;
  }
  fclose(f);
  return 0;
}

static int path_join2(char *dst, size_t cap, const char *a, const char *b)
{
  int n = snprintf(dst, cap, "%s/%s", a, b);

  return (n > 0 && (size_t) n < cap) ? 0 : -1;
}

static int path_join3(char *dst, size_t cap, const char *a, const char *b,
                      const char *c)
{
  int n = snprintf(dst, cap, "%s/%s/%s", a, b, c);

  return (n > 0 && (size_t) n < cap) ? 0 : -1;
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

static int dimm_mem_type_is_hbm(const char *mem_type)
{
  if (mem_type == NULL || mem_type[0] == '\0')
    return 0;
  return strstr(mem_type, "HBM") != NULL || strstr(mem_type, "hbm") != NULL;
}

static void visit_dimm(const char *mcpath, const char *dimm_name,
                       host_edac_dimm_fn fn, void *ctx,
                       int *has_ddr, int *has_hbm)
{
  char speed_path[384];
  char type_path[384];
  char type_line[128];
  long long mtps = 0;
  int is_hbm = 0;

  if (path_join3(speed_path, sizeof(speed_path), mcpath, dimm_name,
                 "dimm_mem_speed") != 0)
    return;
  if (read_long_long_from_file(speed_path, &mtps) != 0 || mtps <= 0)
    return;

  if (path_join3(type_path, sizeof(type_path), mcpath, dimm_name,
                 "dimm_mem_type") == 0
      && read_first_line(type_path, type_line, sizeof(type_line)) == 0)
    is_hbm = dimm_mem_type_is_hbm(type_line);

  if (has_ddr != NULL || has_hbm != NULL) {
    if (is_hbm) {
      if (has_hbm != NULL)
        *has_hbm = 1;
    } else if (has_ddr != NULL) {
      *has_ddr = 1;
    }
  }
  if (fn != NULL)
    fn(mtps, is_hbm, ctx);
}

static void walk_edac_mc(host_edac_dimm_fn fn, void *ctx,
                         int *has_ddr, int *has_hbm)
{
  DIR *mcdir;
  struct dirent *mc;
  const char *root = host_edac_mc_root();

  mcdir = opendir(root);
  if (mcdir == NULL)
    return;
  while ((mc = readdir(mcdir)) != NULL) {
    DIR *dimm_dir;
    struct dirent *dimm;
    char mcpath[256];

    if (strncmp(mc->d_name, "mc", 2) != 0)
      continue;
    if (path_join2(mcpath, sizeof(mcpath), root, mc->d_name) != 0)
      continue;
    dimm_dir = opendir(mcpath);
    if (dimm_dir == NULL)
      continue;
    while ((dimm = readdir(dimm_dir)) != NULL) {
      if (strncmp(dimm->d_name, "dimm", 4) != 0)
        continue;
      visit_dimm(mcpath, dimm->d_name, fn, ctx, has_ddr, has_hbm);
    }
    closedir(dimm_dir);
  }
  closedir(mcdir);
}

int host_edac_scan_mem_classes(int *has_ddr, int *has_hbm)
{
  if (has_ddr != NULL)
    *has_ddr = 0;
  if (has_hbm != NULL)
    *has_hbm = 0;
  walk_edac_mc(NULL, NULL, has_ddr, has_hbm);
  return 0;
}

int host_edac_foreach_dimm(host_edac_dimm_fn fn, void *ctx)
{
  if (fn == NULL)
    return -1;
  walk_edac_mc(fn, ctx, NULL, NULL);
  return 0;
}
