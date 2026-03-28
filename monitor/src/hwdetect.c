#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <dirent.h>
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

/* lspci can omit the word "Infiniband" for some Mellanox lines; class 0207 is InfiniBand. */
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

void auto_disable_optional_stats_by_lspci(void)
{
  int has_nvidia_gpu = 0;
  int has_amd_gpu = 0;
  int has_ib = 0;
  int has_opa = 0;
  FILE *fp = popen("lspci -nn 2>/dev/null", "r");
  char line[1024];
  if (fp == NULL) {
    TRACE("lspci not available; keeping optional types enabled\n");
    return;
  }

  while (fgets(line, sizeof(line), fp) != NULL) {
    to_lower_ascii(line);
    if (strstr(line, "vga compatible controller") != NULL ||
        strstr(line, "3d controller") != NULL ||
        strstr(line, "display controller") != NULL ||
        strstr(line, "processing accelerators") != NULL) {
      if (strstr(line, "nvidia") != NULL)
        has_nvidia_gpu = 1;
      if (strstr(line, "advanced micro devices") != NULL ||
          strstr(line, " amd/ati ") != NULL)
        has_amd_gpu = 1;
    }
    if (strstr(line, "infiniband") != NULL || strstr(line, "[0207]") != NULL)
      has_ib = 1;
    if (strstr(line, "omnipath") != NULL || strstr(line, "hfi") != NULL)
      has_opa = 1;
  }
  pclose(fp);

  if (!has_ib)
    has_ib = infiniband_sysfs_has_devices();

  if (!has_nvidia_gpu)
    disable_type_if_present("nvidia_gpu");
  if (!has_amd_gpu)
    disable_type_if_present("amd_gpu");
  if (!has_ib) {
    disable_type_if_present("ib");
    disable_type_if_present("ib_ext");
    disable_type_if_present("ib_sw");
  }
  if (!has_opa)
    disable_type_if_present("opa");
}
